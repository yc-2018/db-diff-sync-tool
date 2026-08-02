# -*- coding: utf-8 -*-
"""Build the Windows distribution with Unicode-safe PyInstaller arguments."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import PyInstaller.__main__


ROOT = Path(__file__).resolve().parent
# PyInstaller itself still receives command-line-like values internally.  Keep
# its staging names ASCII, then rename the finished distribution with Path,
# which handles the Unicode user-facing name reliably on Windows.
INTERNAL_NAME = "db_diff_sync_tool"
APP_NAME = "数据库同步比对工具"
DIST_DIR = ROOT / "dist"
INTERNAL_DIST = DIST_DIR / INTERNAL_NAME
FINAL_DIST = DIST_DIR / APP_NAME
ORACLE_CLIENT = ROOT / ".oracle_client" / "instantclient_21_22"
ICON_FILE = ROOT / "web" / "app-icon.ico"


def main() -> None:
    if not ICON_FILE.is_file():
        raise FileNotFoundError(f"Missing application icon: {ICON_FILE}")

    os.chdir(ROOT)
    args = [
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        INTERNAL_NAME,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(ROOT / "build" / INTERNAL_NAME),
        "--specpath",
        str(ROOT / "build"),
        "--icon",
        str(ICON_FILE),
        "--collect-all",
        "oracledb",
        "--add-data",
        f"{ROOT / 'web'}{os.pathsep}web",
    ]

    if (ORACLE_CLIENT / "oci.dll").is_file():
        print("Oracle Instant Client found; including it in the package.")
        args.extend(
            [
                "--add-data",
                f"{ORACLE_CLIENT}{os.pathsep}.oracle_client{os.sep}instantclient_21_22",
            ]
        )
    else:
        print("Oracle Instant Client not found; the package will use Oracle thin mode.")

    args.append("app.py")
    PyInstaller.__main__.run(args)
    if not INTERNAL_DIST.is_dir():
        raise FileNotFoundError(f"PyInstaller output not found: {INTERNAL_DIST}")
    if FINAL_DIST.exists():
        shutil.rmtree(FINAL_DIST)
    INTERNAL_DIST.rename(FINAL_DIST)
    internal_exe = FINAL_DIST / f"{INTERNAL_NAME}.exe"
    final_exe = FINAL_DIST / f"{APP_NAME}.exe"
    if internal_exe.is_file():
        internal_exe.rename(final_exe)
    print(f"Package completed: {FINAL_DIST}")


if __name__ == "__main__":
    main()
