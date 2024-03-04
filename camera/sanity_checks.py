#!/usr/bin/env python3

# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# Automation of camera sanity commands found in: https://fburl.com/wiki/rp19mtyq

import inspect
import logging as log
import os
import sys
import types
import typing


# A bit of a hack, but allow natural access of shared modules in the parent directory
# in this script:
script_file = inspect.getfile(typing.cast(types.FrameType, inspect.currentframe()))
current_dir = os.path.dirname(os.path.abspath(script_file))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)


from neo_test import getNeoADBSession  # noqa: E402 - import

# Documentation for the 'camcapture' tool: https://fburl.com/wiki/opxqrz3p


def test_snapshot_jpeg():
    adb = getNeoADBSession()
    adb.assert_disable_vendor_MCS()

    adb.assert_camcapture([])
    adb.assert_camcapture(['-d', '4032x3024', '-e', '33333,200'])
    adb.assert_camcapture(['-e', '33333,100', '-w', '1.0,1.0,1.0,3.0'])
    adb.assert_camcapture(['-d', '4032x3024', '-s', '-12,0,12,6'])
    adb.assert_camcapture(['-d', '4000x3000', '-D', 'auto'])
    adb.assert_camcapture(['-d', '4032x3024', '-n', '10', '-K'])
    adb.assert_camcapture(['-d', '4032x3024', '-D', 'hdr', '-i'])
    adb.assert_camcapture(['-i', '-X', '2'])
    adb.assert_camcapture(['-d', '1920x1080', '-R', '90'])


def test_snapshot_raw():
    adb = getNeoADBSession()
    adb.assert_disable_vendor_MCS()
    adb.assert_camcapture(['-d', '4032x3024', '-r', '-e', '33333,500'])


def test_video():
    adb = getNeoADBSession()
    adb.assert_disable_vendor_MCS()
    log.info("NB: Video tests are long running, plz be patient ^_^:")
    # -x <milliseconds>
    adb.assert_camcapture(['-m'], timeout_sec=20)
    adb.assert_camcapture(['-m', '-v', 'avc', '-R', '90', '-x', '60000'], timeout_sec=80)
    adb.assert_camcapture(['-d', '1280x720', '-m', '-x', '5000'], timeout_sec=20)
    adb.assert_camcapture(['-d', '1920x1080', '-m', '-x', '10000', '-e', '33333,100', '-w', '1.0,1.0,3.0,1.0'], timeout_sec=25)
    adb.assert_camcapture(['-m', '-x', '10000', '-o', 'none'], timeout_sec=30)


def test_snapshot_heic():
    adb = getNeoADBSession()
    adb.assert_disable_vendor_MCS()

    adb.assert_camcapture(['-o', 'heic', '-n', '20'])
    adb.assert_camcapture(['-o', 'heic', '-D', 'auto'])
    adb.assert_camcapture(['-o', 'heic', '-q', '99'])
    adb.assert_camcapture(['-o', 'heic', '-e', '33333,200'])
    adb.assert_camcapture(['-o', 'heic', '-w', '1.0,3.0,3.0,1.0'])
    adb.assert_camcapture(['-o', 'heic', '-w', '1.0,3.0,3.0,1.0', '-e', '33333,250', '-n', '3'])
    adb.assert_camcapture(['-o', 'heic', '-n', '10', '-K'])

    # adb.assert_enable_vendor_MCS()
