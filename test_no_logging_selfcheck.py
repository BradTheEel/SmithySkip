"""Check the no-logging test with simulated loads that write relative paths."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock, patch
import os

import test_no_logging


class NoLoggingSelfCheck(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.dll_path = root / "input.dll"
        self.dll_path.write_bytes(b"placeholder")
        self.scratch = root / "scratch"
        self.caller = root / "caller"
        self.caller.mkdir()
        # A misplaced relative write must not affect the scratch checks.
        (self.caller / "SmithySkip.log").write_bytes(b"caller log")
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(self.caller)

        self.free_library = Mock(return_value=1)
        kernel32 = SimpleNamespace(FreeLibrary=self.free_library)
        replace_windll = patch.object(test_no_logging.ctypes, "windll", SimpleNamespace(kernel32=kernel32))
        replace_windll.start()
        self.addCleanup(replace_windll.stop)

    def run_check(self, write_log=None):
        def load(_dll_path):
            if write_log is not None:
                write_log(Path("SmithySkip.log"))
            return SimpleNamespace(_handle=0x123456789)

        with patch.object(test_no_logging.ctypes, "WinDLL", side_effect=load):
            test_no_logging.main(self.dll_path, self.scratch)

    def test_accepts_unchanged_logs(self):
        self.run_check()
        self.assertEqual(Path.cwd(), self.caller)

    def test_rejects_log_created_in_empty_directory(self):
        with self.assertRaises(AssertionError):
            self.run_check(lambda log: log.write_bytes(b"relative write"))
        self.assertEqual(Path.cwd(), self.caller)

    def test_rejects_seeded_log_content_change(self):
        def change_content(log):
            if log.is_file():
                before = log.stat()
                log.write_bytes(b"changed test log")
                os.utime(log, ns=(before.st_atime_ns, before.st_mtime_ns))

        with self.assertRaises(AssertionError):
            self.run_check(change_content)
        self.assertEqual(Path.cwd(), self.caller)

    def test_rejects_seeded_log_timestamp_change(self):
        def change_timestamp(log):
            if log.is_file():
                before = log.stat()
                os.utime(log, ns=(before.st_atime_ns, before.st_mtime_ns - 2_000_000_000))

        with self.assertRaises(AssertionError):
            self.run_check(change_timestamp)
        self.assertEqual(Path.cwd(), self.caller)

    def test_restores_cwd_when_load_raises(self):
        failure = OSError("simulated DLL load failure")
        with patch.object(test_no_logging.ctypes, "WinDLL", side_effect=failure):
            with self.assertRaises(OSError) as caught:
                test_no_logging.main(self.dll_path, self.scratch)
        self.assertIs(caught.exception, failure)
        self.assertEqual(Path.cwd(), self.caller)

    def test_rejects_failed_unload_and_restores_cwd(self):
        self.free_library.return_value = 0
        with self.assertRaises(OSError):
            self.run_check()
        self.assertEqual(Path.cwd(), self.caller)


if __name__ == "__main__":
    main(verbosity=2)
