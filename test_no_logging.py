"""Check production DLL log output through the owned native-test launcher."""

from pathlib import Path
import argparse
import ctypes
import os
import shutil


def load_and_unload(dll_path: Path) -> None:
    free_library = ctypes.windll.kernel32.FreeLibrary
    free_library.argtypes = [ctypes.c_void_p]
    free_library.restype = ctypes.c_int
    previous_cwd = Path.cwd()
    try:
        os.chdir(dll_path.parent)
        plugin = ctypes.WinDLL(str(dll_path))
        if not free_library(plugin._handle):
            raise OSError(f"FreeLibrary failed for {dll_path}")
    finally:
        os.chdir(previous_cwd)


def main(dll_path: Path, scratch: Path) -> None:
    dll_path = dll_path.resolve(strict=True)
    scratch = scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    if any(scratch.iterdir()):
        raise ValueError(f"Scratch directory must be empty: {scratch}")

    for mode in ("empty", "seeded"):
        directory = scratch / mode
        directory.mkdir()
        isolated_dll = directory / "SmithySkip.dll"
        log_path = directory / "SmithySkip.log"
        shutil.copy2(dll_path, isolated_dll)

        before = None
        if mode == "seeded":
            log_path.write_bytes(b"preserve-existing-log\r\n")
            before = (log_path.read_bytes(), log_path.stat().st_mtime_ns)

        load_and_unload(isolated_dll)

        if before is None:
            if log_path.exists():
                raise AssertionError("Production DLL created SmithySkip.log in the empty directory")
        elif not log_path.is_file() or (log_path.read_bytes(), log_path.stat().st_mtime_ns) != before:
            raise AssertionError("Production DLL changed the seeded SmithySkip.log content or mtime")

    print("PASS: no new SmithySkip.log, and seeded log bytes and mtime preserved")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dll", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()
    main(args.dll, args.scratch)
