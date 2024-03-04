#!/usr/bin/env python3

# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# Extends adb_utils.py for Neo family of devices (Hammerhead + Greatwhite)

from enum import Enum
import logging as log
import os
import platform
import shutil
import subprocess
import typing
from typing import List, Optional


from adb_test import ADBAssertionError, ADBSession, ADBTestSession, ADBTestClassFactory, CommandResult, InitializationError, TestClassFactory, TestSession, executable_exists, run_command  # noqa: E402 - import


NEO_TEST_VERSION = "0.2"         # Version of this code, which we log
ONEWIRE_PATH: Optional[str] = None
ONEWIRE_VERSION: str = "2023_03_22"
DISPLAYTOOL_COMMAND: list = []
DISPLAYTOOL_CWD: Optional[str] = None


def adb_platform() -> str:
    python_platform = platform.system()
    return "macosx" if python_platform == "Darwin" else python_platform.lower()


def hs_python_path() -> str:
    hs_python_bin = os.environ.get('HSPYTHON', None)
    if hs_python_bin is not None:
        return hs_python_bin
    return 'python3' if not adb_platform == "windows" else 'python'


def run_onewire_tool(cmd: List[str], timeout_sec=10, utf8=True) -> CommandResult:
    if adb_platform() == "windows":
        # Windows:  onewire_host can't be run directly as it starts with Unix
        # style shebang "#!/usr/bin/env python3" ... need to be run with Python.
        return run_command([hs_python_path()] + [ONEWIRE_PATH] + cmd, timeout_sec, utf8, DISPLAYTOOL_CWD)

    return run_command([ONEWIRE_PATH] + cmd, timeout_sec, utf8, DISPLAYTOOL_CWD)


def run_display_tool(cmd: List[str], timeout_sec=10, utf8=True) -> CommandResult:
    return run_command(DISPLAYTOOL_COMMAND + cmd, timeout_sec, utf8, DISPLAYTOOL_CWD)


def find_neo_utils() -> None:
    if ONEWIRE_PATH is not None:
        return  # paths already found

    script_path = os.path.dirname(os.path.realpath(__file__))
    adb_plat = adb_platform()

    def onewire_exists_at_path(path_components) -> bool:
        global ONEWIRE_PATH
        onewire_path = os.path.join(*path_components)
        if executable_exists(onewire_path):
            ONEWIRE_PATH = onewire_path
            result = run_onewire_tool(cmd=['--build_info'])
            if result.succeeded and result.stderr_contains(r"onewire_host.py build info:"):
                version = result.stderr_search(r"version=v([0-9.-]+)\n")
                ONEWIRE_PATH = shutil.which(os.path.normpath(onewire_path))
                log.info(f"OneWire {version} found at {ONEWIRE_PATH}")
                return True
        ONEWIRE_PATH = None
        return False

    # NB: As of 3/22 drop, only MacOS still using .pex version:
    onewire_paths = [
        [script_path, 'onewire', ONEWIRE_VERSION, adb_plat, 'onewire_host.pex'],
        [script_path, 'onewire', ONEWIRE_VERSION, adb_plat, 'onewire_host'],
        [script_path, 'onewire', ONEWIRE_VERSION, adb_plat, 'onewire_host.exe'],
        ['onewire_host.pex'],
        ['onewire_host'],
        ['onewire_host.exe'],
    ]

    for potential_path in onewire_paths:
        if onewire_exists_at_path(potential_path):
            break

    if ONEWIRE_PATH is None:
        raise InitializationError('no suitable OneWire found')

    dt_path = os.path.join(script_path, '..', 'greatwhite', 'display', 'src')
    python_bin = hs_python_path()
    global DISPLAYTOOL_COMMAND
    DISPLAYTOOL_COMMAND = [python_bin, '-m', 'display.displaytool']
    global DISPLAYTOOL_CWD
    DISPLAYTOOL_CWD = os.path.normpath(dt_path)
    cr = run_display_tool(['--help'])
    if cr.succeeded:
        log.info(f"Display tool invokable with '{' '.join(DISPLAYTOOL_COMMAND)}' in '{DISPLAYTOOL_CWD}'")
        return

    # Fallback to running the manually installed version of the tool:
    DISPLAYTOOL_COMMAND = ['display']
    DISPLAYTOOL_CWD = None  # cwd shouldn't matter in this case
    cr = run_display_tool(['--help'])
    if not cr.succeeded:
        raise InitializationError('no suitable display tool found')
    log.info(f"Display tool invokable with '{' '.join(DISPLAYTOOL_COMMAND)}' in '{DISPLAYTOOL_CWD}'")


class NeoCommandResult(CommandResult):
    """Allow natural interaction with the result of a command.
    The assert_* methods throw exceptions, allowing validation without a bunch
    of conditional logic.
    """

    def __init__(self, command, completed_proc) -> None:
        super().__init__(command, completed_proc)

    def assert_mfg_succeeded(self) -> None:
        """Assert JSON returned by 'mfg' command indicates a success."""
        self.assert_succeeded()
        self.assert_json()
        if self.json_data['status'] != "Success":
            raise ADBAssertionError(
                f"FAILED: '{self.command_str}' didn't report 'Success', JSON:\n{self.completed_proc.stdout}")


class DeviceType(Enum):
    HAMMERHEAD = 'hammerhead'
    GREATWHITE = 'greatwhite'


class BoardType(Enum):
    SN_UNKNOWN = 0
    HN_UNKNOWN = 1
    SN_DEV0 = 160
    SN_P1 = 161  # and P1_1
    SN_DEV0_1 = 162
    SN_RESERVED = 163
    SN_EVT1 = 164
    SN_EVT1_1 = 165
    HN_CONFIG_DEV0 = 176
    HN_CONFIG_DEV0_1 = 177
    HN_PREP1 = 178
    # Something else missing?  Add it here and at https://fburl.com/wiki/214u6mge


class NeoADBSession(ADBSession):
    """Interact with a neo device via ADB, identified by serial number.
    NOTE: There can be more than one device attached.
    """
    factory_mode: bool = False
    device_type: DeviceType
    board_type: BoardType
    have_oem_device_info: bool = False
    vendor_mcs_enabled: bool = True
    oem_device_info: dict[str, str] = {}

    def __init__(self, factory: TestClassFactory, test_session: TestSession) -> None:
        super().__init__(factory, test_session)

    def refresh_device_state(self) -> None:
        super().refresh_device_state()
        self.factory_mode = self.is_factory_mode()
        log.info(f"Device in factory mode: {'YES' if self.factory_mode else 'NO'}")
        # Dump sgdeviceid stuff:
        self.assert_log_prop("ro.product.device")
        self.assert_log_prop("ro.build.flavor")
        self.assert_log_prop("ro.build.fingerprint")
        self.assert_log_shell_command(['uname', '-a'])

        # MCU:
        # self.refresh_mcu_state()

        log.info("Wireless Firmware:")
        golden_bin = '/vendor/firmware_mnt/image/kiwi/bdwlan.elf'
        if self.root:
            self.assert_log_shell_command(label="sha1sum", cmd=['sha1sum', golden_bin])
            self.assert_log_shell_command(label="file size", cmd=['stat', '-c', '%s', golden_bin])
        else:
            log.info(f"Need ADB root to access {golden_bin}")

        board_id: int = 0

        try:
            bt_file = '/sys/devices/soc0/platform_subtype_id'
            self.device_type = DeviceType(self.device_name)
            if self.root:
                self.board_type = BoardType(self.assert_file_int(bt_file))
            else:
                log.info(f"Need ADB root to access {bt_file}")
                self.board_type = BoardType.SN_UNKNOWN if self.device_type == DeviceType.HAMMERHEAD else BoardType.HN_UNKNOWN
        except KeyError:
            raise ADBAssertionError(f"FAILED: '{self.device_name}' or platform_subtype_id={board_id} doesn't map to a known configuration: update test infra")
        log.info(f"Device type = {self.device_type}, {self.board_type}")

    def refresh_mcu_state(self) -> None:
        # NB: If the MCU is in a crash loop, these will fail:
        self.assert_log_shell_command(label="build_info", cmd=['tdb', 'shell', 'build_info'])
        self.assert_log_shell_command(label="board_info", cmd=['tdb', 'shell', 'board_info'])
        self.assert_log_shell_command(label="adc board_id", cmd=['tdb', 'shell', 'adc', 'board_id'])

    def is_factory_mode(self) -> bool:
        if not self.root:
            return False        # We need root to cat /proc/cmdline to tell
        result = self.run_shell_command(['cat', '/proc/cmdline'])
        result.assert_succeeded()
        return result.contains("androidboot.factorytest=1")

    def on_root(self) -> None:
        super().on_root()
        # Refresh factory mode state cache, because we have root now so we can tell:
        self.factory_mode = self.is_factory_mode()
        pass

    def reboot_to_fastboot(self) -> None:
        self.test_session.prompt_user_validate("Initiate device reset")
        log.info("Rebooting device ...")
        self.run_command(['reboot', 'bootloader'])
        msg = r"""Use serial console to verify device is in 'fastboot'
On the serial console, look for the line:
    Dev_Common_Speed: Dev Bus Speed: High, state 2"""
        self.test_session.prompt_user_timed_process(msg, timeout_secs=30)

    def set_factory_mode(self, factory_mode: bool = True) -> None:
        self.reboot_to_fastboot()
        self.run_fastboot_command(['oem', 'enable-factory-mode' if factory_mode else 'disable-factory-mode'])
        self.run_fastboot_command(['reboot'])
        self.wait_for_device(wait_root=True)
        self.factory_mode = factory_mode
        mode_txt = 'factory' if factory_mode else 'normal (non-factory)'
        log.info(f"Device is now in {mode_txt} mode")

    def assert_factory_mode(self) -> None:
        self.assert_root()
        if self.factory_mode:
            return
        log.info("Note: device needs to be in 'factory' mode for the following steps -- this requires a reboot of the device:")
        self.set_factory_mode()

    def assert_oem_device_info(self) -> None:
        if self.have_oem_device_info:
            return
        self.reboot_to_fastboot()
        r = self.run_fastboot_command(['oem', 'device-info'])
        r.assert_succeeded()
        self.run_fastboot_command(['reboot'])
        self.wait_for_device()

    def run_mfg_command(self, cmd: list, timeout_sec=10, utf8=True) -> NeoCommandResult:
        """Run 'adb shell mfg' command"""
        return typing.cast(NeoCommandResult, self.run_shell_command(['mfg'] + cmd))

    def assert_log_mfg_command_str(self, cmd: str) -> None:
        r = self.run_mfg_command(cmd.split(' '))
        r.assert_mfg_succeeded()
        r.assert_log_stdout(new_line=True)

    def is_form_factory_device(self) -> bool:
        if (self.board_type == BoardType.SN_P1 or self.board_type == BoardType.SN_EVT1
           or self.board_type == BoardType.SN_EVT1_1 or self.board_type == BoardType.HN_PREP1):
            return True
        return False

    def device_has_battery(self) -> bool:
        # TODO: it's possible to connect a battery to a dev board, need a flag that
        # can be forced to model that:
        return self.is_form_factory_device()

    def assert_disable_vendor_MCS(self) -> None:
        """In order to use the camcapture tool, we need to turn off vendor MCS, see https://fburl.com/wiki/log09bwm"""
        if not self.vendor_mcs_enabled:
            return              # Already disabled
        self.assert_root()
        self.assert_set_selinux_enforcing(True)
        self.assert_ctl_stop('captureengineservice')
        self.assert_ctl_stop('vendor.camera-provider-2-7')
        self.vendor_mcs_enabled = False

    def assert_enable_vendor_MCS(self) -> None:
        """Reverse disable_vendor_MCS()"""
        self.assert_root()
        self.assert_set_selinux_enforcing(True)
        self.assert_ctl_start('captureengineservice')
        self.assert_ctl_start('vendor.camera-provider-2-7')
        self.vendor_mcs_enabled = True

    def assert_camcapture(self, options: list, timeout_sec=10) -> None:
        """Run 'camcapture' command"""
        self.assert_disable_vendor_MCS()
        self.assert_succeeded(['camcapture', '-c', '0',] + options, timeout_sec)


class NeoADBTestSession(ADBTestSession):
    """Context for running ADB/fastboot tests on a Neo device
    """
    neo_adb: NeoADBSession

    def __init__(self, factory: ADBTestClassFactory, attended_mode: bool = True,
                 device_serial: Optional[str] = None) -> None:
        super().__init__(factory, attended_mode, device_serial)
        log.info(f"Neo ADB Testing Framework ver {NEO_TEST_VERSION}")
        self.neo_adb = typing.cast(NeoADBSession, self.adb)
        find_neo_utils()


class NeoADBTestClassFactory(ADBTestClassFactory):
    @classmethod
    def createCommandResult(cls, command: list, completed_proc: subprocess.CompletedProcess) -> CommandResult:
        return NeoCommandResult(command, completed_proc)

    @classmethod
    def createADBSession(cls, factory: TestClassFactory, test_session: TestSession) -> ADBSession:
        return NeoADBSession(factory, test_session)


global_session: Optional[NeoADBTestSession] = None


def getNeoADBTestSession(attended_mode: bool = True) -> NeoADBTestSession:
    global global_session
    if global_session is not None:
        return global_session
    factory = NeoADBTestClassFactory()
    global_session = NeoADBTestSession(factory, attended_mode)
    return global_session


def getNeoADBSession(attended_mode: bool = True, echo_commands: bool = True) -> NeoADBSession:
    session = getNeoADBTestSession(attended_mode)
    adb = session.neo_adb
    adb.echo_commands = echo_commands
    return adb


# A basic test for this module:
if __name__ == "__main__":
    try:
        session = getNeoADBTestSession(attended_mode=True)
        adb = session.neo_adb
        adb.echo_commands = True
        adb.assert_factory_mode()

        r = adb.run_mfg_command(['led', 'probe'])
        r.assert_mfg_succeeded()
    except Exception as e:
        log.exception(e)
