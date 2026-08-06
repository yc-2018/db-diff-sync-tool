# -*- coding: utf-8 -*-
"""Build the Windows distribution with Unicode-safe PyInstaller arguments."""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

import PyInstaller.__main__


ROOT = Path(__file__).resolve().parent
# PyInstaller itself still receives command-line-like values internally.  Keep
# its staging names ASCII, then rename the finished distribution with Path,
# which handles the Unicode user-facing name reliably on Windows.
INTERNAL_NAME = "db_diff_sync_tool"
MCP_SERVER_NAME = "dbsync_mcp_server"
MCP_CONFIG_NAME = "dbsync_mcp_configurator"
APP_NAME = "数据库同步比对工具"
DIST_DIR = ROOT / "dist"
ORACLE_CLIENT_ROOT = ROOT / ".oracle_client"
ICON_FILE = ROOT / "web" / "app-icon.ico"


def find_oracle_client() -> Path | None:
    """Find a locally supplied Instant Client directory."""
    candidates = sorted(ORACLE_CLIENT_ROOT.glob("instantclient_*"))
    for candidate in candidates:
        if (candidate / "oci.dll").is_file():
            return candidate
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--app-name",
        default=APP_NAME,
        help="Final distribution directory and executable name.",
    )
    parser.add_argument(
        "--oracle-client-dir",
        type=Path,
        help="Instant Client directory to include. Defaults to .oracle_client/instantclient_*.",
    )
    parser.add_argument(
        "--skip-oracle-client",
        action="store_true",
        help="Build a thin-mode-only package even when an Instant Client is present.",
    )
    parser.add_argument(
        "--require-oracle-client",
        action="store_true",
        help="Fail the build unless the selected Instant Client contains oci.dll.",
    )
    return parser.parse_args()


def main() -> None:
    options = parse_args()
    if not ICON_FILE.is_file():
        raise FileNotFoundError(f"Missing application icon: {ICON_FILE}")
    if options.skip_oracle_client and options.require_oracle_client:
        raise ValueError("--skip-oracle-client cannot be used with --require-oracle-client")

    internal_dist = DIST_DIR / INTERNAL_NAME
    final_dist = DIST_DIR / options.app_name
    oracle_client = None
    if not options.skip_oracle_client:
        oracle_client = options.oracle_client_dir or find_oracle_client()
        if oracle_client is not None:
            oracle_client = oracle_client.resolve()

    if options.require_oracle_client and (
        oracle_client is None or not (oracle_client / "oci.dll").is_file()
    ):
        raise FileNotFoundError("Required Oracle Instant Client was not found or lacks oci.dll.")

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

    oracle_data_args = []
    if oracle_client is not None and (oracle_client / "oci.dll").is_file():
        print(f"Oracle Instant Client found; including it in the package: {oracle_client}")
        oracle_data_args = [
            "--add-data",
            f"{oracle_client}{os.pathsep}.oracle_client{os.sep}{oracle_client.name}",
        ]
        args.extend(oracle_data_args)
    else:
        print("Oracle Instant Client not found; the package will use Oracle thin mode.")

    args.append("app.py")
    PyInstaller.__main__.run(args)
    if not internal_dist.is_dir():
        raise FileNotFoundError(f"PyInstaller output not found: {internal_dist}")
    if final_dist.exists():
        shutil.rmtree(final_dist)
    internal_dist.rename(final_dist)
    internal_exe = final_dist / f"{INTERNAL_NAME}.exe"
    final_exe = final_dist / f"{options.app_name}.exe"
    if internal_exe.is_file():
        internal_exe.rename(final_exe)

    # Build the MCP stdio executable and its configuration-center UI as
    # separate onedir packages. Keeping their runtimes in separate folders
    # avoids collisions with the desktop app's PyInstaller _internal folder.
    mcp_build_root = ROOT / "build"
    server_dist = mcp_build_root / "mcp_server_dist"
    config_dist = mcp_build_root / "mcp_configurator_dist"
    for path in (server_dist, config_dist):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)

    server_args = [
        "--noconfirm", "--clean", "--console", "--onefile",
        "--name", MCP_SERVER_NAME,
        "--distpath", str(server_dist),
        "--workpath", str(mcp_build_root / MCP_SERVER_NAME),
        "--specpath", str(mcp_build_root),
        "--collect-all", "oracledb",
        *oracle_data_args,
        "mcp/server.py",
    ]
    PyInstaller.__main__.run(server_args)
    server_exe = server_dist / f"{MCP_SERVER_NAME}.exe"
    if not server_exe.is_file():
        raise FileNotFoundError(f"MCP server output not found: {server_exe}")

    config_internal_dist = config_dist / MCP_CONFIG_NAME
    config_args = [
        "--noconfirm", "--clean", "--windowed",
        "--name", MCP_CONFIG_NAME,
        "--distpath", str(config_dist),
        "--workpath", str(mcp_build_root / MCP_CONFIG_NAME),
        "--specpath", str(mcp_build_root),
        "--icon", str(ICON_FILE),
        "--add-data", f"{ROOT / 'mcp'}{os.pathsep}mcp",
        "mcp/configurator.py",
    ]
    PyInstaller.__main__.run(config_args)
    if not config_internal_dist.is_dir():
        raise FileNotFoundError(f"MCP configurator output not found: {config_internal_dist}")

    mcp_target = final_dist / "mcp"
    server_target = mcp_target / "server"
    config_target = mcp_target / "config"
    server_target.mkdir(parents=True, exist_ok=True)
    config_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(server_exe, server_target / "dbsync-mcp.exe")
    config_internal_dist.rename(config_target)
    config_exe = config_target / f"{MCP_CONFIG_NAME}.exe"
    if config_exe.is_file():
        config_exe.rename(config_target / "dbsync-mcp-configurator.exe")
    print(f"MCP server and configurator packaged under: {mcp_target}")
    print(f"Package completed: {final_dist}")


if __name__ == "__main__":
    main()
