#!/usr/bin/env python3

# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# Testing framework for Android devices using adb/fastboot style commands

import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import logging as log

from typing import List, Optional


ADB_UTILS_VERSION = "0.1"
ADB_PLATFORM_VERSION = "33.0.3"   # Version of the Android platform tools which are checked into this repo and are tested.

MIN_PYTHON = (3, 8)            # 3.8: Latest widely supported FB Python, 3.10+: match/case syntax

# Check min Python version early, before we attempt to use any features that could
# break the interpreter in an ugly manner:
if sys.version_info < MIN_PYTHON:
    sys.exit("Python %s.%s or later is required.\n" % MIN_PYTHON)


class Error(Exception):
    """Base-class for all exceptions raised by this module."""


class InitializationError(Error):
    """There was a problem initializing the module."""


class ADBAssertionError(Error):
    """One of the ADB util assertions on a command result failed."""


class CommandResult:
    """Allow natural interaction with the result of a command.
    The assert_* methods throw exceptions, allowing validation without a bunch
    of conditional logic.
    """

    def __init__(self, command: List[str], completed_proc: subprocess.CompletedProcess):
        self.command = command
        self.completed_proc = completed_proc

    @property
    def succeeded(self) -> bool:
        return self.completed_proc.returncode == 0

    @property
    def command_str(self) -> str:
        return ' '.join(self.command)

    def failure_message(self) -> str:
        if self.succeeded:
            return f"SUCCEEDED: {self.command_str}"
        msg = f"FAILED ({self.completed_proc.returncode}): {self.command_str}\n"
        msg += f"STDERR={self.completed_proc.stderr}STDOUT={self.completed_proc.stdout}"
        return msg

    def assert_succeeded(self) -> None:
        """Assert return code of the process is 0 == SUCCESS"""
        if not self.succeeded:
            raise ADBAssertionError(self.failure_message())

    def assert_log_stdout(self, new_line: bool = False) -> None:
        self.assert_succeeded()
        log.info(('\n' if new_line else '') + self.completed_proc.stdout)

        # Regex oriented asserts:
        # -----------------------

    def contains(self, pattern: str) -> bool:
        """Return true if regex matches stdout of the command"""
        return re.search(pattern, self.completed_proc.stdout, re.MULTILINE) is not None

    def stderr_contains(self, pattern: str) -> bool:
        """return true if regex matches stderr of the command"""
        return re.search(pattern, self.completed_proc.stderr, re.MULTILINE) is not None

    def assert_contains(self, pattern: str) -> None:
        if not self.contains(pattern):
            raise ADBAssertionError(
                f"FAILED: '{self.command_str}' stdout was supposed to match regex '{pattern}', but didn't.\nSTDOUT was:\n{self.completed_proc.stdout}")

    def search(self, pattern: str) -> Optional[str]:
        """Return first capture group of regex, or None if regex fails to match"""
        re_result = re.search(pattern, self.completed_proc.stdout, re.MULTILINE)
        return None if re_result is None else str(re_result.group(1))

    def stderr_search(self, pattern: str) -> Optional[str]:
        """Return first capture group of regex, or None if regex fails to match"""
        re_result = re.search(pattern, self.completed_proc.stderr, re.MULTILINE)
        return None if re_result is None else str(re_result.group(1))

    def assert_search(self, pattern: str) -> str:
        res = self.search(pattern)
        if res is None:
            raise ADBAssertionError(
                f"FAILED: '{self.command_str}' stdout was supposed to match regex '{pattern}', but didn't.\nSTDOUT was:\n{self.completed_proc.stdout}")
        return res

    def assert_string(self) -> str:
        self.assert_succeeded()
        str_val: str = self.completed_proc.stdout
        if len(str_val.strip()) == 0 or len(str_val.splitlines()) > 1:
            raise ADBAssertionError(
                f"FAILED: '{self.command_str}' stdout was supposed to be a single string, but wasn't.\nSTDOUT was:\n{self.completed_proc.stdout}")
        return str_val.strip()

    def assert_int(self) -> int:
        try:
            return int(self.assert_string())
        except ValueError:
            raise ADBAssertionError(
                f"FAILED: '{self.command_str}' stdout was supposed to be an integer, but wasn't.\nSTDOUT was:\n{self.completed_proc.stdout}")

    def assert_stderr_keyvalue(self, pattern: str) -> dict[str, str]:
        self.assert_succeeded()
        kv = {}
        for line in self.completed_proc.stderr.split():
            re_result = re.search(pattern, line)
            if re_result is None:
                continue
            kv[re_result.group(1)] = re_result.group(2)
        return kv

        # Methods for commands which return JSON:
        # ---------------------------------------

    def assert_json(self) -> None:
        """Assert that the command returned valid JSON, store it for further assertions"""
        try:
            self.json_data = json.loads(self.completed_proc.stdout)
        except json.decoder.JSONDecodeError as e:
            raise ADBAssertionError(
                f"FAILED: '{self.command_str}' stdout was supposed to contain valid json, but didn't.\nSTDOUT was:\n{self.completed_proc.stdout}.  Decoding error was: {e.msg}")


def run_command(cmd: list, timeout_sec=10, utf8=True, cwd: Optional[str] = None) -> CommandResult:
    cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=utf8, timeout=timeout_sec, cwd=cwd)
    return CommandResult(cmd, cp)


def executable_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def prompt_user(prompt: str) -> str:
    return input(prompt)


ADB_PATH = None
FASTBOOT_PATH = None


def find_platform_utils() -> None:
    if ADB_PATH is not None:
        return  # paths already found

    script_path = os.path.dirname(os.path.realpath(__file__))
    log.info(f"Meta ADB Test Framework ver {ADB_UTILS_VERSION}")
    log.info(f"Testing starting from {script_path}, version={ADB_UTILS_VERSION}")
    python_platform = platform.system()
    adb_platform = "macosx" if python_platform == "Darwin" else python_platform.lower()

    def adb_exists_at_path(path_components) -> bool:
        adb_path = os.path.join(*path_components)
        log.info(adb_path)
        if executable_exists(adb_path):
            result = run_command(cmd=[adb_path, '--version'])
            if result.succeeded and result.contains(r"Android Debug Bridge"):
                version = result.search(r"Version ([0-9.-]+)\n")
                global ADB_PATH
                ADB_PATH = shutil.which(os.path.normpath(adb_path))
                log.info(f"ADB {version} found at {ADB_PATH}")
                return True
        return False

    adb_paths = [
        [script_path, '..', 'android-platform-tools', ADB_PLATFORM_VERSION, adb_platform, 'adb'],
        [script_path, '..', 'android-platform-tools', ADB_PLATFORM_VERSION, adb_platform, 'adb.exe'],
        ['adb'],
        ['adb.exe'],
    ]

    for potential_path in adb_paths:
        if adb_exists_at_path(potential_path):
            break

    if ADB_PATH is None:
        raise InitializationError('no suitable ADB found')

    def fastboot_exists_at_path(path_components) -> bool:
        adb_path = os.path.join(*path_components)
        if executable_exists(adb_path):
            result = run_command(cmd=[adb_path, '--version'])
            if result.succeeded and result.contains(r"fastboot version"):
                version = result.search(r"fastboot version ([0-9.-]+)\n")
                global FASTBOOT_PATH
                FASTBOOT_PATH = shutil.which(os.path.normpath(adb_path))
                log.info(f"fastboot {version} found at {FASTBOOT_PATH}")
                return True
        return False

    fastboot_paths = [
        [script_path, '..', 'android-platform-tools', ADB_PLATFORM_VERSION, adb_platform, 'fastboot'],
        [script_path, '..', 'android-platform-tools', ADB_PLATFORM_VERSION, adb_platform, 'fastboot.exe'],
        ['fastboot'],
        ['fastboot.exe'],
    ]

    for potential_path in fastboot_paths:
        if fastboot_exists_at_path(potential_path):
            break

    if FASTBOOT_PATH is None:
        raise InitializationError('no suitable fastboot found')


def run_adb_command(cmd: List[str], timeout_sec=10, utf8=True) -> CommandResult:
    return run_command([ADB_PATH] + cmd, timeout_sec, utf8)


LOGGING_INITIALIZED = False


def initialize_logging():
    global LOGGING_INITIALIZED
    if LOGGING_INITIALIZED:
        return

    # create formatter
    formatter = log.Formatter('%(asctime)s %(message)s')
    formatter.datefmt = "%Y-%m-%dT%H:%M:%S%z"

    # StreamHandler logs to stderr:
    sh = log.StreamHandler()
    sh.setFormatter(formatter)

    # Also create a log file to make runs easy to upload
    
    log_name = 'adb-test-{:%Y-%m-%d}.log'.format(datetime.datetime.now())
    log_file_name = os.path.join(tempfile.gettempdir(), log_name)
    print(f"Logging to: {log_file_name}")
    fh = log.FileHandler(filename=log_file_name, mode='w')  # w == overwrite log files vs append
    fh.setFormatter(formatter)

    # We will use the root level 'Logger' instance:
    rl = log.getLogger()
    rl.setLevel(log.INFO)
    rl.addHandler(sh)
    rl.addHandler(fh)

    LOGGING_INITIALIZED = True


class TestSession:
    """Context for running tests.
    """

    def __init__(self, attended_mode=True):
        self.attended = attended_mode
        initialize_logging()
        find_platform_utils()

    def prompt_user_timed_process(self, wait_reason: str, timeout_secs: float = 0) -> None:
        """Prompt the user to confirm that some process has completed.  In unattended mode, just wait 'timeout_secs' and then continue"""
        if self.attended:
            log.info(f"{wait_reason} (Estimated to take < {timeout_secs} secs)")
            prompt_user("Press ENTER to continue ...")
        else:
            log.info(f"{wait_reason} - unattended mode, waiting {timeout_secs} sec then continuing ... ")
            time.sleep(timeout_secs)

    def prompt_user_validate(self, confirm_item: str) -> None:
        """Prompt the user to confirm some manual test item.  In unattended mode, just continue and assume it's ok"""
        if self.attended:
            log.info(confirm_item)
            prompt_user("Press ENTER to confirm & continue ...")
        else:
            log.info(f"Unattended mode: skipping manual validation of '{confirm_item}'...")


class TestClassFactory:
    @classmethod
    def createCommandResult(cls, command: List[str], completed_proc: subprocess.CompletedProcess) -> CommandResult:
        return CommandResult(command, completed_proc)


class ADBSession:
    """Interact with a device via ADB, identified by serial number.
    NOTE: There can be more than one device attached.
    """
    factory: TestClassFactory
    test_session: TestSession
    device_name: Optional[str] = None
    device_serial: Optional[str] = None
    # Device state:
    root: bool = False
    remounted: bool = False
    echo_commands: bool = False

    def __init__(self, factory: TestClassFactory, test_session: TestSession) -> None:
        self.factory = factory
        self.test_session = test_session

    def bind_serial(self, device_serial: str) -> None:
        self.device_serial = device_serial
        log.info(f"Started ADB session with device serial = {self.device_serial}")
        self.refresh_device_state()

    def refresh_device_state(self) -> None:
        self.root = self.is_root()
        self.remounted = self.is_remounted()
        self.device_name = self.assert_get_prop('ro.product.device')
        log.info(f"Device name: {self.device_name}")
        log.info(f"ADB running as root: {'YES' if self.root else 'NO'}")
        log.info(f"/system & /vendor remounted RW: {'YES' if self.remounted else 'NO'}")

    def bind_with_first_device(self) -> None:
        result = run_adb_command(['devices'])
        result.assert_succeeded()
        result.assert_contains('^List of devices attached\n')
        device_serial = result.search(r'^(\w+)\s+device')
        if device_serial is None:
            raise ADBAssertionError("'adb devices' shows no connected devices available to run tests")
        return self.bind_serial(device_serial)

    def run_cli_command(self, cmd: list, timeout_sec=10, utf8=True) -> CommandResult:
        cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=utf8, timeout=timeout_sec)
        return self.factory.createCommandResult(cmd, cp)

    def run_command(self, cmd: list, timeout_sec=10, utf8=True) -> CommandResult:
        """Run ADB command"""
        cmd = ['-s', self.device_serial] + cmd
        if self.echo_commands:
            log.info("Running: " + ' '.join(['adb'] + cmd))
        return self.run_cli_command([ADB_PATH] + cmd, timeout_sec, utf8)

    def run_fastboot_command(self, cmd: list, timeout_sec=10, utf8=True) -> CommandResult:
        """Run fastboot command"""
        cmd = ['-s', self.device_serial] + cmd
        if self.echo_commands:
            log.info("Running: " + ' '.join(['fastboot'] + cmd))
        return self.run_cli_command([FASTBOOT_PATH] + cmd, timeout_sec, utf8)

    def run_shell_command(self, cmd: list, timeout_sec=10, utf8=True) -> CommandResult:
        """Run 'adb shell' command"""
        return self.run_command(['shell'] + cmd, timeout_sec, utf8)

    def assert_succeeded(self, cmd: list, timeout_sec=10, utf8=True) -> None:
        """Run 'adb shell' command, assert success"""
        r = self.run_shell_command(cmd, timeout_sec, utf8)
        r.assert_succeeded()

    def is_root(self) -> bool:
        result = self.run_shell_command(['id', '-u'])
        result.assert_succeeded()
        uid = result.assert_int()
        return uid == 0

    def assert_root(self) -> None:
        if self.root:
            return
        log.info("Need root, running 'adb root' to elevate privilege ...")
        result = self.run_command(['root'])
        result.assert_succeeded()
        self.root = True
        log.info("'adb root' -> success.")
        self.on_root()

    def on_root(self) -> None:
        """Called when transitioning to ADB root"""
        pass

    def is_remounted(self) -> bool:
        result = self.run_shell_command(['mount'])
        result.assert_succeeded()
        return result.contains("overlay on /system type overlay")

    def unroot(self) -> None:
        result = self.run_command(['unroot'])
        result.assert_succeeded()
        self.root = False

    def assert_remount(self) -> None:
        if self.remounted:
            return
        result = self.run_command(['remount'])
        result.assert_succeeded()
        self.remounted = True

    def wait_for_device(self, wait_root: bool = False):
        log.info("Waiting for ADB to come back (adb wait-for-device) ...")
        cmd = ['wait-for-device']
        if wait_root:
            cmd += ['root']
        result = self.run_command(cmd, timeout_sec=60)
        result.assert_succeeded()
        log.info("ADB is up again.")

    def assert_log_shell_command(self, cmd: list, label: Optional[str] = None) -> None:
        """Log string result of shell command, with optional label"""
        r = self.run_shell_command(cmd)
        cmd_output = r.assert_string()
        log.info(f"{label if label is not None else cmd[0]}={cmd_output}")

    def assert_log_shell_command_str(self, cmd: str, label: Optional[str] = None) -> None:
        """Log string result of shell command, with optional label"""
        return self.assert_log_shell_command(cmd.split(' '), label)

    def assert_string(self, cmd: list) -> str:
        """Return string from shell command"""
        r = self.run_shell_command(cmd)
        return r.assert_string()

    def assert_file_string(self, filename: str) -> str:
        """Get the string contents of a file on the device"""
        return self.assert_string(['cat', filename])

    def assert_log_file_string(self, filename: str) -> None:
        """Log file string value value"""
        file_string: str = self.assert_file_string(filename)
        log.info(f"{filename}={file_string}")

    def assert_file_int(self, filename_str: str) -> int:
        """Get the (assumed) integer stored in a file on the device"""
        r = self.run_shell_command(['cat', filename_str])
        return r.assert_int()

    def assert_get_prop(self, prop_name: str) -> str:
        """Return Android property"""
        return self.assert_string(['getprop', prop_name])

    def assert_set_prop(self, prop_name: str, prop_value: str) -> None:
        """Set Android property"""
        return self.assert_succeeded(['setprop', prop_name, prop_value])

    def assert_log_prop(self, prop_name: str) -> None:
        """Log Android property value"""
        prop_value: str = self.assert_get_prop(prop_name)
        log.info(f"{prop_name}={prop_value}")

    def is_selinux_enforcing(self) -> bool:
        """Get SE Linux enforcing mode"""
        mode = self.assert_string(['getenforce'])
        return True if mode == "Enforcing" else False

    def assert_set_selinux_enforcing(self, enforcing: bool) -> None:
        """Set SE Linux enforcing mode"""
        if self.is_selinux_enforcing() == enforcing:
            return              # Already set
        self.assert_root()
        return self.assert_succeeded(['setenforce', '1' if enforcing else '0'])

    def assert_ctl_stop(self, service: str) -> None:
        """Stop service"""
        return self.assert_set_prop('ctl.stop', service)

    def assert_ctl_start(self, service: str) -> None:
        """Stop service"""
        return self.assert_set_prop('ctl.start', service)


class ADBTestClassFactory(TestClassFactory):
    @classmethod
    def createADBSession(cls, factory: TestClassFactory, test_session: TestSession) -> ADBSession:
        return ADBSession(factory, test_session)


# Note: ADBTestSession deriving off of TestSession here:  Partially it's to get around
# the fact that you can't really have forward class references in Python, and
# we want the TestSession to have a reference to the ADBSession and vice versa.


class ADBTestSession(TestSession):
    """Context for running ADB/fastboot tests
    """

    adb: ADBSession

    def __init__(self, factory: ADBTestClassFactory, attended_mode: bool = True,
                 device_serial: Optional[str] = None) -> None:
        super().__init__(attended_mode)
        self.adb = factory.createADBSession(factory, self)
        if device_serial is None:
            self.adb.bind_with_first_device()
        else:
            self.adb.bind_serial(device_serial)


# A basic test for this module:
if __name__ == "__main__":
    try:
        factory = ADBTestClassFactory()
        session = ADBTestSession(factory, attended_mode=True)
        session.adb.assert_root()
        session.adb.echo_commands = True
    except Exception as e:
        log.exception(e)
