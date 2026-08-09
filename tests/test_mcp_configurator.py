# -*- coding: utf-8 -*-
"""Configuration center helpers do not duplicate existing MCP entries."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))
import configurator


assert not hasattr(configurator.ConfiguratorApi(), "window")


with tempfile.TemporaryDirectory() as tmp:
    bundle_root = Path(tmp)
    page = bundle_root / "mcp" / "configurator.html"
    page.parent.mkdir()
    page.write_text("<!doctype html>", encoding="utf-8")
    had_meipass = hasattr(configurator.sys, "_MEIPASS")
    original_meipass = getattr(configurator.sys, "_MEIPASS", None)
    try:
        configurator.sys._MEIPASS = str(bundle_root)
        assert configurator.resource_path("configurator.html") == page
    finally:
        if had_meipass:
            configurator.sys._MEIPASS = original_meipass
        else:
            del configurator.sys._MEIPASS


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    executable = root / "configurator.exe"
    internal = root / "_internal"
    internal.mkdir()
    for name in ("runtime.dll", "helper.exe", "extension.pyd", "readme.txt"):
        (internal / name).write_bytes(b"")
    original_frozen = getattr(configurator.sys, "frozen", False)
    original_executable = configurator.sys.executable
    removed = []
    original_remove = configurator.os.remove
    try:
        configurator.sys.frozen = True
        configurator.sys.executable = str(executable)
        configurator.os.remove = lambda path: removed.append(path)
        configurator.unblock_bundled_runtime_files()
    finally:
        configurator.os.remove = original_remove
        configurator.sys.executable = original_executable
        configurator.sys.frozen = original_frozen
    assert {Path(path).name for path in removed} == {
        "runtime.dll:Zone.Identifier",
        "helper.exe:Zone.Identifier",
        "extension.pyd:Zone.Identifier",
    }


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    codex_config = root / ".codex" / "config.toml"
    assert configurator._write_codex_project_config(codex_config, "python.exe", ["server.py"])
    assert configurator._toml_has_server(codex_config)
    assert not configurator._write_codex_project_config(codex_config, "python.exe", ["server.py"])

    opencode_config = root / "opencode.json"
    assert configurator._write_opencode_config(opencode_config, "python.exe", ["server.py"])
    assert configurator._json_has_server(opencode_config, "mcp")
    assert json.loads(opencode_config.read_text(encoding="utf-8"))["mcp"]["dbsync"]["enabled"] is True
    assert not configurator._write_opencode_config(opencode_config, "python.exe", ["server.py"])

print("===== MCP 配置中心辅助测试通过 =====")
