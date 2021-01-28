#!/usr/bin/env python3

import sys
try:
    import gi
    import re
    import os

    from gi.repository import GLib, Gio

    import unittest
    import socket
    import struct
    import subprocess
    import shutil
    import traceback
    import glob
    import tempfile
except Exception as e:
    print("Missing dependencies: %s" % str(e))
    sys.exit(77)

FPrint = None

# Re-run the test with the passed wrapper if set
wrapper = os.getenv('LIBFPRINT_TEST_WRAPPER')
if wrapper:
    wrap_cmd = wrapper.split(' ') + [sys.executable, os.path.abspath(__file__)] + \
        sys.argv[1:]
    os.unsetenv('LIBFPRINT_TEST_WRAPPER')
    sys.exit(subprocess.check_call(wrap_cmd))

ctx = GLib.main_context_default()


class Connection:

    def __init__(self, addr):
        self.addr = addr

    def __enter__(self):
        self.con = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.con.connect(self.addr)
        return self.con

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.con.close()
        del self.con


class GLibErrorMessage:
    def __init__(self, component, level, expected_message):
        self.level = level
        self.component = component
        self.expected_message = expected_message

    def __enter__(self):
        GLib.test_expect_message(self.component, self.level, self.expected_message)

    def __exit__(self, exc_type, exc_val, exc_tb):
        (filename, line, procname, text) = traceback.extract_stack()[-2]
        GLib.test_assert_expected_messages_internal(self.component,
            filename, line, procname)

class VirtualDevice(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        unittest.TestCase.setUpClass()
        cls.tmpdir = tempfile.mkdtemp(prefix='libfprint-')

        driver_name = cls.driver_name if hasattr(cls, 'driver_name') else None
        if not driver_name:
            driver_name = re.compile(r'(?<!^)(?=[A-Z])').sub(
                '_', cls.__name__).lower()

        sock_name = driver_name.replace('_', '-')
        cls.sockaddr = os.path.join(cls.tmpdir, '{}.socket'.format(sock_name))
        os.environ['FP_{}'.format(driver_name.upper())] = cls.sockaddr

        cls.ctx = FPrint.Context()

        cls.dev = None
        for dev in cls.ctx.get_devices():
            # We might have a USB device in the test system that needs skipping
            if dev.get_driver() == driver_name:
                cls.dev = dev
                break

        assert cls.dev is not None, "You need to compile with {} for testing".format(driver_name)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir)
        del cls.dev
        del cls.ctx
        unittest.TestCase.tearDownClass()

    def setUp(self):
        super().setUp()
        self._close_on_teardown = True
        self.assertFalse(self.dev.is_open())
        self.dev.open_sync()
        self.assertTrue(self.dev.is_open())

    def tearDown(self):
        if self._close_on_teardown:
            self.assertTrue(self.dev.is_open())
            self.dev.close_sync()
        self.assertFalse(self.dev.is_open())
        super().tearDown()

    def wait_timeout(self, interval):
        timeout_reached = False
        def on_timeout():
            nonlocal timeout_reached
            timeout_reached = True

        GLib.timeout_add(interval, on_timeout)
        while not timeout_reached:
            ctx.iteration(False)

    def send_command(self, command, *args):
        self.assertIn(command, ['INSERT', 'REMOVE', 'SCAN', 'ERROR', 'RETRY',
            'FINGER', 'UNPLUG', 'SLEEP', 'SET_ENROLL_STAGES', 'SET_SCAN_TYPE',
            'SET_CANCELLATION_ENABLED'])

        with Connection(self.sockaddr) as con:
            params = ' '.join(str(p) for p in args)
            con.sendall('{} {}'.format(command, params).encode('utf-8'))

        while ctx.pending():
            ctx.iteration(False)

    def send_finger_report(self, has_finger, iterate=True):
        self.send_command('FINGER', 1 if has_finger else 0)

        if iterate:
            expected = (FPrint.FingerStatusFlags.PRESENT if has_finger
                else ~FPrint.FingerStatusFlags.PRESENT)

            while not (self.dev.get_finger_status() & expected):
                ctx.iteration(True)

    def send_error(self, error):
        self.assertIsInstance(error, FPrint.DeviceError)
        self.send_command('ERROR', int(error))

    def send_retry(self, retry):
        self.assertIsInstance(retry, FPrint.DeviceRetry)
        self.send_command('RETRY', int(retry))

    def send_auto(self, obj):
        if isinstance(obj, FPrint.DeviceError):
            self.send_error(obj)
        elif isinstance(obj, FPrint.DeviceRetry):
            self.send_retry(obj)
        elif isinstance(obj, FPrint.FingerStatusFlags):
            self.send_finger_report(obj & FPrint.FingerStatusFlags.PRESENT, iterate=False)
        elif isinstance(obj, FPrint.ScanType):
            self.send_command('SET_SCAN_TYPE', obj.value_nick)
        else:
            raise Exception('No known type found for {}'.format(obj))

    def send_sleep(self, interval):
        self.assertGreater(interval, 0)
        multiplier = 5 if 'UNDER_VALGRIND' in os.environ else 1
        self.send_command('SLEEP', interval * multiplier)

    def enroll_print(self, nick, finger, username='testuser', retry_scan=-1):
        self._enrolled = None

        def done_cb(dev, res):
            print("Enroll done")
            try:
                self._enrolled = dev.enroll_finish(res)
            except Exception as e:
                self._enrolled = e

        self._enroll_stage = -1
        def progress_cb(dev, stage, pnt, data, error):
            self._enroll_stage = stage
            self._enroll_progress_error = error

        self.assertLessEqual(retry_scan, self.dev.get_nr_enroll_stages())

        retries = 1
        should_retry = retry_scan > 0

        def enroll_in_progress():
            if self._enroll_stage < 0 and not self._enrolled:
                return True

            if isinstance(self._enrolled, Exception):
                raise(self._enrolled)

            nonlocal retries
            self.assertLessEqual(self._enroll_stage, self.dev.get_nr_enroll_stages())
            if should_retry and retries > retry_scan:
                self.assertEqual(self._enroll_stage, retries - 1)
            else:
                self.assertEqual(self._enroll_stage, retries)

            if retries == retry_scan + 1:
                self.assertIsNotNone(self._enroll_progress_error)
                self.assertEqual(self._enroll_progress_error.code, FPrint.DeviceRetry.TOO_SHORT)
            else:
                self.assertIsNone(self._enroll_progress_error)

            if self._enroll_stage < self.dev.get_nr_enroll_stages():
                self._enroll_stage = -1
                self.assertIsNone(self._enrolled)
                self.assertEqual(self.dev.get_finger_status(),
                    FPrint.FingerStatusFlags.NEEDED)
                if retry_scan == retries:
                    GLib.idle_add(self.send_auto, FPrint.DeviceRetry.TOO_SHORT)
                else:
                    GLib.idle_add(self.send_command, 'SCAN', nick)
                retries += 1

            return not self._enrolled

        self.assertEqual(self.dev.get_finger_status(), FPrint.FingerStatusFlags.NONE)

        self.send_command('SCAN', nick)

        template = FPrint.Print.new(self.dev)
        template.set_finger(finger)
        template.set_username(username)

        self.dev.enroll(template, callback=done_cb, progress_cb=progress_cb)
        while enroll_in_progress():
            ctx.iteration(False)

        self.assertEqual(self._enroll_stage, retries if not should_retry else retries - 1)
        self.assertEqual(self._enroll_stage, self.dev.get_nr_enroll_stages())
        self.assertEqual(self.dev.get_finger_status(), FPrint.FingerStatusFlags.NONE)

        self.assertEqual(self._enrolled.get_device_stored(),
            self.dev.has_storage())

        return self._enrolled

    def start_verify(self, p, identify=False):
        self._verify_match = None
        self._verify_fp = None
        self._verify_error = None
        self._verify_report_match = None
        self._verify_report_print = None
        self._verify_completed = False
        self._verify_reported = False
        self._cancellable = Gio.Cancellable()

        if identify:
            self.assertTrue(self.dev.supports_identify())

        def match_cb(dev, match, pnt, data, error):
            self._verify_reported = True
            self._verify_report_match = match
            self._verify_report_print = pnt
            self._verify_report_error = error

        def verify_cb(dev, res):
            try:
                self._verify_match, self._verify_fp = (
                    dev.identify_finish(res) if identify else dev.verify_finish(res))
            except gi.repository.GLib.Error as e:
                self._verify_error = e

            self._verify_completed = True

        if identify:
            self.dev.identify(p if isinstance(p, list) else [p],
                cancellable=self._cancellable, match_cb=match_cb, callback=verify_cb)
        else:
            self.dev.verify(p, cancellable=self._cancellable, match_cb=match_cb,
                callback=verify_cb)

    def cancel_verify(self):
        self._cancellable.cancel()
        while not self._verify_completed:
            ctx.iteration(True)

        self.assertIsNone(self._verify_match)
        self.assertIsNotNone(self._verify_error)
        self.assertEqual(self.dev.get_finger_status(), FPrint.FingerStatusFlags.NONE)

    def complete_verify(self):
        while not self._verify_completed:
            ctx.iteration(True)

        if self._verify_error is not None:
            raise self._verify_error

    def check_verify(self, p, scan_nick, match, identify=False):
        if isinstance(scan_nick, str):
            self.send_command('SCAN', scan_nick)
        elif scan_nick is not None:
            self.send_auto(scan_nick)

        self.start_verify(p, identify)
        self.complete_verify()

        self.assertTrue(self._verify_reported)

        if not match:
            self.assertIsNone(self._verify_report_match)

        if identify:
            if match:
                self.assertIsNotNone(self._verify_report_match)
                self.assertIsNotNone(self._verify_match)
        else:
            if self._verify_fp:
                self.assertEqual(self._verify_fp.equal(p), match)
                if match:
                    self.assertTrue(
                        self._verify_fp.equal(self._verify_report_match))
            else:
                self.assertFalse(match)

        if isinstance(scan_nick, str):
            self.assertEqual(self._verify_fp.props.fpi_data.get_string(), scan_nick)

    def test_device_properties(self):
        self.assertEqual(self.dev.get_driver(), 'virtual_device')
        self.assertEqual(self.dev.get_device_id(), '0')
        self.assertEqual(self.dev.get_name(), 'Virtual device for debugging')
        self.assertTrue(self.dev.is_open())
        self.assertEqual(self.dev.get_scan_type(), FPrint.ScanType.SWIPE)
        self.assertEqual(self.dev.get_nr_enroll_stages(), 5)
        self.assertFalse(self.dev.supports_identify())
        self.assertFalse(self.dev.supports_capture())
        self.assertFalse(self.dev.has_storage())

    def test_enroll(self):
        matching = self.enroll_print('testprint', FPrint.Finger.LEFT_LITTLE)
        self.assertEqual(matching.get_username(), 'testuser')
        self.assertEqual(matching.get_finger(), FPrint.Finger.LEFT_LITTLE)

    def test_enroll_with_retry(self):
        matching = self.enroll_print('testprint', FPrint.Finger.LEFT_LITTLE, retry_scan=2)
        self.assertEqual(matching.get_username(), 'testuser')
        self.assertEqual(matching.get_finger(), FPrint.Finger.LEFT_LITTLE)

    def test_enroll_verify_match(self):
        matching = self.enroll_print('testprint', FPrint.Finger.LEFT_THUMB)

        self.check_verify(matching, 'testprint', match=True,
            identify=self.dev.supports_identify())

    def test_enroll_verify_no_match(self):
        matching = self.enroll_print('testprint', FPrint.Finger.LEFT_RING)

        if self.dev.has_storage():
            with self.assertRaisesRegex(GLib.Error, 'Print was not found'):
                self.check_verify(matching, 'not-testprint', match=False,
                    identify=self.dev.supports_identify())
        else:
            self.check_verify(matching, 'not-testprint', match=False,
                identify=self.dev.supports_identify())

    def test_enroll_verify_error(self):
        matching = self.enroll_print('testprint', FPrint.Finger.LEFT_RING)

        with self.assertRaisesRegex(GLib.Error, r"An unspecified error occurred"):
            self.check_verify(matching, FPrint.DeviceError.GENERAL, match=False,
                identify=self.dev.supports_identify())

    def test_enroll_verify_retry(self):
        with self.assertRaisesRegex(GLib.GError, 'too short'):
            self.check_verify(FPrint.Print.new(self.dev),
                FPrint.DeviceRetry.TOO_SHORT, match=False)

    def test_finger_status(self):
        self.start_verify(FPrint.Print.new(self.dev),
            identify=self.dev.supports_identify())

        self.assertEqual(self.dev.get_finger_status(),
                         FPrint.FingerStatusFlags.NEEDED)

        self.send_finger_report(True)
        self.assertEqual(self.dev.get_finger_status(),
            FPrint.FingerStatusFlags.NEEDED | FPrint.FingerStatusFlags.PRESENT)

        self.send_finger_report(False)
        self.assertEqual(self.dev.get_finger_status(), FPrint.FingerStatusFlags.NEEDED)

        self.cancel_verify()

    def test_finger_status_after_sleep(self):
        self.send_sleep(10)
        self.start_verify(FPrint.Print.new(self.dev),
                          identify=self.dev.supports_identify())

        self.assertEqual(self.dev.get_finger_status(),
                         FPrint.FingerStatusFlags.NONE)

        while self.dev.get_finger_status() != FPrint.FingerStatusFlags.NEEDED:
            ctx.iteration(True)

        self.assertEqual(self.dev.get_finger_status(),
                         FPrint.FingerStatusFlags.NEEDED)

        self.send_finger_report(True)
        self.assertEqual(self.dev.get_finger_status(),
                         FPrint.FingerStatusFlags.NEEDED | FPrint.FingerStatusFlags.PRESENT)

        self.send_finger_report(False)
        self.assertEqual(self.dev.get_finger_status(),
                         FPrint.FingerStatusFlags.NEEDED)

        self.cancel_verify()

    def test_change_enroll_stages(self):
        notified_spec = None
        def on_stage_changed(dev, spec):
            nonlocal notified_spec
            notified_spec = spec

        self.dev.connect('notify::nr-enroll-stages', on_stage_changed)

        notified_spec = None
        self.send_command('SET_ENROLL_STAGES', 20)
        self.assertEqual(self.dev.get_nr_enroll_stages(), 20)
        self.assertEqual(notified_spec.name, 'nr-enroll-stages')

        notified_spec = None
        self.send_command('SET_ENROLL_STAGES', 1)
        self.assertEqual(self.dev.get_nr_enroll_stages(), 1)
        self.assertEqual(notified_spec.name, 'nr-enroll-stages')

        with GLibErrorMessage('libfprint-device',
            GLib.LogLevelFlags.LEVEL_CRITICAL, '*enroll_stages > 0*'):
            notified_spec = None
            self.send_command('SET_ENROLL_STAGES', 0)
            self.assertEqual(self.dev.get_nr_enroll_stages(), 1)
            self.assertIsNone(notified_spec)

    def test_quick_enroll(self):
        self.send_command('SET_ENROLL_STAGES', 1)
        self.assertEqual(self.dev.get_nr_enroll_stages(), 1)
        matching = self.enroll_print('testprint', FPrint.Finger.LEFT_LITTLE)
        self.assertEqual(matching.get_username(), 'testuser')
        self.assertEqual(matching.get_finger(), FPrint.Finger.LEFT_LITTLE)

    def test_change_scan_type(self):
        notified_spec = None
        def on_scan_type_changed(dev, spec):
            nonlocal notified_spec
            notified_spec = spec

        self.dev.connect('notify::scan-type', on_scan_type_changed)

        for scan_type in [FPrint.ScanType.PRESS, FPrint.ScanType.SWIPE]:
            notified_spec = None
            self.send_auto(scan_type)
            self.assertEqual(self.dev.get_scan_type(), scan_type)
            self.assertEqual(notified_spec.name, 'scan-type')

        with GLibErrorMessage('libfprint-virtual_device',
            GLib.LogLevelFlags.LEVEL_WARNING, '*Scan type*not found'):
            notified_spec = None
            self.send_command('SET_SCAN_TYPE', 'eye-contact')
            self.assertEqual(self.dev.get_scan_type(), FPrint.ScanType.SWIPE)
            self.assertIsNone(notified_spec)

    def test_device_unplug(self):
        self._close_on_teardown = False
        notified_spec = None
        def on_removed_notify(dev, spec):
            nonlocal notified_spec
            notified_spec = spec

        removed = False
        def on_removed(dev):
            nonlocal removed
            removed = True

        self.assertFalse(self.dev.props.removed)

        self.dev.connect('notify::removed', on_removed_notify)
        self.dev.connect('removed', on_removed)
        self.send_command('UNPLUG')
        self.assertEqual(notified_spec.name, 'removed')
        self.assertTrue(self.dev.props.removed)
        self.assertTrue(removed)

        with self.assertRaisesRegex(GLib.GError, 'device has been removed from the system'):
            self.dev.close_sync()

    def test_device_unplug_during_verify(self):
        self._close_on_teardown = False

        notified_spec = None
        def on_removed_notify(dev, spec):
            nonlocal notified_spec
            notified_spec = spec

        removed = False
        def on_removed(dev):
            nonlocal removed
            removed = True

        self.assertFalse(self.dev.props.removed)
        self.dev.connect('notify::removed', on_removed_notify)
        self.dev.connect('removed', on_removed)

        self.start_verify(FPrint.Print.new(self.dev),
            identify=self.dev.supports_identify())

        self.send_command('UNPLUG')
        self.assertEqual(notified_spec.name, 'removed')
        self.assertTrue(self.dev.props.removed)
        self.assertFalse(removed)

        with self.assertRaisesRegex(GLib.GError, 'device has been removed from the system'):
            self.complete_verify()

        self.assertTrue(removed)

        with self.assertRaisesRegex(GLib.GError, 'device has been removed from the system'):
            self.dev.close_sync()

    def test_device_sleep(self):
        self.send_sleep(1500)

        self.start_verify(FPrint.Print.new(self.dev),
            identify=self.dev.supports_identify())

        self.wait_timeout(300)
        self.assertFalse(self._verify_completed)

        self._cancellable.cancel()
        self.wait_timeout(200)

        self.assertTrue(self._verify_completed)
        self.cancel_verify()

    def test_device_sleep_on_cancellation(self):
        self.send_command('SET_CANCELLATION_ENABLED', int(False))
        self.send_sleep(1500)
        self.send_command('SCAN', 'foo-print')

        self.start_verify(FPrint.Print.new(self.dev),
            identify=self.dev.supports_identify())
        self.wait_timeout(300)

        self.assertFalse(self._verify_completed)

        self._cancellable.cancel()
        self.wait_timeout(300)

        self.assertFalse(self._verify_completed)
        self.cancel_verify()

        # Since we don't really cancel here, next command will be passed to release
        self._close_on_teardown = False
        with GLibErrorMessage('libfprint-virtual_device',
            GLib.LogLevelFlags.LEVEL_WARNING, 'Could not process command: SCAN *'):
            self.dev.close_sync()

    def test_device_sleep_before_completing_verify(self):
        enrolled = self.enroll_print('foo-print', FPrint.Finger.LEFT_RING)

        self.send_sleep(100)
        self.start_verify(enrolled, identify=self.dev.supports_identify())
        self.send_command('SCAN', 'bar-print')
        self.send_sleep(800)

        while not self._verify_reported:
            ctx.iteration(False)

        self.assertFalse(self._verify_completed)
        self.wait_timeout(10)
        self.assertFalse(self._verify_completed)

        if self.dev.has_storage():
            with self.assertRaisesRegex(GLib.Error, 'Print was not found'):
                self.complete_verify()
        else:
            self.complete_verify()
        self.assertTrue(self._verify_reported)

    def test_close_error(self):
        self._close_on_teardown = False
        close_res = None

        def on_closed(dev, res):
            nonlocal close_res
            try:
                close_res = dev.close_finish(res)
            except GLib.Error as e:
                close_res = e

        self.send_sleep(100)
        self.send_error(FPrint.DeviceError.BUSY)
        self.dev.close(callback=on_closed)
        self.wait_timeout(2)
        self.assertIsNone(close_res)

        while not close_res:
            ctx.iteration(True)

        self.assertEqual(close_res.code, int(FPrint.DeviceError.BUSY))

class VirtualDeviceStorage(VirtualDevice):

    def tearDown(self):
        self.cleanup_device_storage()
        super().tearDown()

    def cleanup_device_storage(self):
        if self.dev.is_open() and not self.dev.props.removed:
            for print in self.dev.list_prints_sync():
                self.assertTrue(self.dev.delete_print_sync(print, None))

    def test_device_properties(self):
        self.assertEqual(self.dev.get_driver(), 'virtual_device_storage')
        self.assertEqual(self.dev.get_device_id(), '0')
        self.assertEqual(self.dev.get_name(),
            'Virtual device with storage and identification for debugging')
        self.assertTrue(self.dev.is_open())
        self.assertEqual(self.dev.get_scan_type(), FPrint.ScanType.SWIPE)
        self.assertEqual(self.dev.get_nr_enroll_stages(), 5)
        self.assertTrue(self.dev.supports_identify())
        self.assertFalse(self.dev.supports_capture())
        self.assertTrue(self.dev.has_storage())

    def test_duplicate_enroll(self):
        self.enroll_print('testprint', FPrint.Finger.LEFT_LITTLE)
        with self.assertRaisesRegex(GLib.Error, 'finger has already enrolled'):
            self.enroll_print('testprint', FPrint.Finger.LEFT_LITTLE)

    def test_list_empty(self):
        self.assertFalse(self.dev.list_prints_sync())

    def test_list_populated(self):
        self.send_command('INSERT', 'p1')
        print2 = self.enroll_print('p2', FPrint.Finger.LEFT_LITTLE)
        self.assertEqual({'p1', 'p2'}, {p.props.fpi_data.get_string() for p in self.dev.list_prints_sync()})

    def test_list_delete(self):
        p = self.enroll_print('testprint', FPrint.Finger.RIGHT_THUMB)
        l = self.dev.list_prints_sync()
        print(l[0])
        self.assertEqual(len(l), 1)
        print('blub', p.props.fpi_data, type(l[0].props.fpi_data))
        assert p.equal(l[0])
        self.dev.delete_print_sync(p)
        self.assertFalse(self.dev.list_prints_sync())

    def test_delete_error(self):
        deleted_res = None
        def on_deleted(dev, res):
            nonlocal deleted_res
            try:
                deleted_res = dev.delete_print_finish(res)
            except GLib.Error as e:
                deleted_res = e

        self.send_sleep(100)
        self.send_error(FPrint.DeviceError.DATA_NOT_FOUND)
        self.dev.delete_print(FPrint.Print.new(self.dev), callback=on_deleted)
        self.wait_timeout(2)
        self.assertIsNone(deleted_res)

        while not deleted_res:
            ctx.iteration(True)

        self.assertEqual(deleted_res.code, int(FPrint.DeviceError.DATA_NOT_FOUND))

    def test_list_error(self):
        list_res = None

        def on_listed(dev, res):
            nonlocal list_res
            try:
                list_res = dev.list_prints_finish(res)
            except GLib.Error as e:
                list_res = e

        self.send_sleep(100)
        self.send_error(FPrint.DeviceError.BUSY)
        self.dev.list_prints(callback=on_listed)
        self.wait_timeout(2)
        self.assertIsNone(list_res)

        while not list_res:
            ctx.iteration(True)

        self.assertEqual(list_res.code, int(FPrint.DeviceError.BUSY))

    def test_list_delete_missing(self):
        p = self.enroll_print('testprint', FPrint.Finger.RIGHT_THUMB)
        self.send_command('REMOVE', 'testprint')

        with self.assertRaisesRegex(GLib.GError, 'Print was not found'):
            self.dev.delete_print_sync(p)

    def test_identify_match(self):
        rt = self.enroll_print('right-thumb', FPrint.Finger.RIGHT_THUMB)
        lt = self.enroll_print('left-thumb', FPrint.Finger.LEFT_THUMB)

        self.check_verify([rt, lt], 'right-thumb', identify=True, match=True)
        self.check_verify([rt, lt], 'left-thumb', identify=True, match=True)

    def test_identify_no_match(self):
        rt = self.enroll_print('right-thumb', FPrint.Finger.RIGHT_THUMB)
        lt = self.enroll_print('left-thumb', FPrint.Finger.LEFT_THUMB)

        self.check_verify(lt, 'right-thumb', identify=True, match=False)
        self.check_verify(rt, 'left-thumb', identify=True, match=False)

    def test_identify_retry(self):
        with self.assertRaisesRegex(GLib.GError, 'too short'):
            self.check_verify(FPrint.Print.new(self.dev),
                FPrint.DeviceRetry.TOO_SHORT, identify=True, match=False)

    def test_delete_multiple_times(self):
        rt = self.enroll_print('right-thumb', FPrint.Finger.RIGHT_THUMB)
        self.dev.delete_print_sync(rt)

        with self.assertRaisesRegex(GLib.Error, 'Print was not found'):
            self.dev.delete_print_sync(rt)

    def test_verify_missing_print(self):
        with self.assertRaisesRegex(GLib.Error, 'Print was not found'):
            self.check_verify(FPrint.Print.new(self.dev),
                'not-existing-print', False, identify=False)

    def test_identify_missing_print(self):
        with self.assertRaisesRegex(GLib.Error, 'Print was not found'):
            self.check_verify(FPrint.Print.new(self.dev),
                              'not-existing-print', False, identify=True)


if __name__ == '__main__':
    try:
        gi.require_version('FPrint', '2.0')
        from gi.repository import FPrint
    except Exception as e:
        print("Missing dependencies: %s" % str(e))
        sys.exit(77)

    # avoid writing to stderr
    unittest.main(testRunner=unittest.TextTestRunner(stream=sys.stdout, verbosity=2))
