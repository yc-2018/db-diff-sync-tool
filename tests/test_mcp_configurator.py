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

    qoder_config = root / "qoder" / "settings.json"
    qoder_config.parent.mkdir()
    qoder_config.write_text(json.dumps({"enabledPlugins": {"demo": True}}), encoding="utf-8")
    assert configurator._write_json_mcp_config(
        qoder_config, {"command": "python.exe", "args": ["server.py"]})
    qoder_data = json.loads(qoder_config.read_text(encoding="utf-8"))
    assert qoder_data["enabledPlugins"] == {"demo": True}
    assert qoder_data["mcpServers"]["dbsync"]["args"] == ["server.py"]
    assert configurator._json_has_server(qoder_config, "mcpServers")
    assert not configurator._write_json_mcp_config(
        qoder_config, {"command": "python.exe", "args": ["server.py"]})


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    json_config = root / "settings.json"
    json_config.write_text(json.dumps({
        "enabledPlugins": {"demo": True},
        "说明": "保留",
        "mcpServers": {
            "dbsync": {"command": "python.exe", "args": ["server.py"]},
            "other": {"command": "other.exe", "args": []},
        },
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert configurator._remove_json_mcp_config(json_config)
    json_data = json.loads(json_config.read_text(encoding="utf-8"))
    assert "dbsync" not in json_data["mcpServers"]
    assert json_data["mcpServers"]["other"]["command"] == "other.exe"
    assert json_data["enabledPlugins"] == {"demo": True}
    assert json_data["说明"] == "保留"
    assert not configurator._remove_json_mcp_config(json_config)

    opencode_config = root / "opencode.json"
    opencode_config.write_text(json.dumps({
        "mcp": {
            "dbsync": {"type": "local", "command": ["python.exe", "server.py"], "enabled": True},
            "other": {"type": "local", "command": ["other.exe"], "enabled": True},
        },
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert configurator._remove_json_mcp_config(opencode_config, "mcp")
    opencode_data = json.loads(opencode_config.read_text(encoding="utf-8"))
    assert "dbsync" not in opencode_data["mcp"]
    assert opencode_data["mcp"]["other"]["enabled"] is True


with tempfile.TemporaryDirectory() as tmp:
    codex_config = Path(tmp) / "config.toml"
    codex_config.write_text(
        "# 顶部注释\n"
        "model = \"demo\"\n"
        "\n"
        "[mcp_servers.dbsync]\n"
        "command = \"python.exe\"\n"
        "args = [\"server.py\"]\n"
        "\n"
        "[mcp_servers.dbsync.env]\n"
        "FOO = \"bar\"\n"
        "\n"
        "[mcp_servers.other]\n"
        "command = \"other.exe\"\n"
        "args = []\n"
        "\n"
        "[profiles.demo]\n"
        "model = \"x\"\n",
        encoding="utf-8")
    assert configurator._toml_has_server(codex_config)
    assert configurator._remove_codex_server_config(codex_config)
    codex_text = codex_config.read_text(encoding="utf-8")
    assert "dbsync" not in codex_text
    assert "# 顶部注释" in codex_text
    assert "[mcp_servers.other]" in codex_text
    assert "[profiles.demo]" in codex_text
    assert not configurator._remove_codex_server_config(codex_config)


with tempfile.TemporaryDirectory() as tmp:
    os.environ["DSH_HOME"] = tmp
    try:
        patch = Path(tmp) / "profiles" / "desktop" / "cordis.patch.yml"
        patch.parent.mkdir(parents=True)
        patch.write_text(
            "- id: demo\n"
            "  config:\n"
            "    enabled: true\n"
            "- insert:\n"
            "    - id: other\n"
            "      name: \"@deepseek-ai/dsh-mcp-client\"\n"
            "      config:\n"
            "        serverName: codegraph\n"
            "        transport: stdio\n"
            "        command: codegraph\n"
            "        args: [ \"serve\", \"--mcp\" ]\n"
            "- id: tail\n"
            "  config:\n"
            "    x: 1\n",
            encoding="utf-8")
        assert not configurator._dsh_installed()
        assert configurator._write_dsh_config("python.exe", ["server.py"])
        assert configurator._dsh_installed()
        text = patch.read_text(encoding="utf-8")
        assert "serverName: dbsync" in text
        assert "serverName: codegraph" in text
        assert text.count("- insert:") == 1
        assert "- id: tail" in text
        assert not configurator._write_dsh_config("python.exe", ["server.py"])

        assert configurator._remove_dsh_config()
        assert not configurator._dsh_installed()
        removed_text = patch.read_text(encoding="utf-8")
        assert "serverName: dbsync" not in removed_text
        assert "serverName: codegraph" in removed_text
        assert "- insert:" in removed_text
        assert "- id: tail" in removed_text
        assert not configurator._remove_dsh_config()
    finally:
        os.environ.pop("DSH_HOME", None)


with tempfile.TemporaryDirectory() as tmp:
    os.environ["DSH_HOME"] = tmp
    try:
        assert configurator._write_dsh_config("python.exe", ["server.py"])
        patch = Path(tmp) / "cordis.patch.yml"
        assert patch.is_file()
        assert configurator._dsh_has_server_text(patch.read_text(encoding="utf-8"))

        assert configurator._remove_dsh_config()
        assert not configurator._dsh_installed()
        removed_text = patch.read_text(encoding="utf-8")
        assert "dbsync" not in removed_text
        assert "- insert:" not in removed_text
        assert not configurator._remove_dsh_config()
    finally:
        os.environ.pop("DSH_HOME", None)


with tempfile.TemporaryDirectory() as tmp:
    config = Path(tmp) / "settings.json"
    config.write_text(json.dumps({
        "mcpServers": {
            "dbsync": {"command": "python.exe", "args": ["server.py"]},
            "other": {"command": "other.exe"},
        },
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    original_scan = configurator.ConfiguratorApi.scan
    original_definitions = configurator.AGENT_DEFINITIONS
    try:
        configurator.AGENT_DEFINITIONS = {
            "fake": {
                "name": "Fake Agent",
                "commands": (),
                "kind": "json",
                "section": "mcpServers",
                "global_config": lambda: config,
                "project_config": None,
                "detect_paths": lambda: [config.parent],
            },
        }
        configurator.ConfiguratorApi.scan = lambda self, project="": {
            "ok": True,
            "project": project,
            "python": "",
            "server": "",
            "agents": [{
                "id": "fake",
                "name": "Fake Agent",
                "installed": True,
                "binary": "",
                "global_installed": configurator._json_has_server(config, "mcpServers"),
                "project_installed": False,
                "project_supported": True,
                "project_selected": bool(project),
                "global_config": str(config),
                "project_config": "",
            }],
        }
        api = configurator.ConfiguratorApi()
        invalid = api.uninstall("fake", "project")
        assert invalid["ok"] is False
        result = api.uninstall("fake")
        assert result["ok"] is True and result["already"] is False
        assert "Fake Agent" in result["msg"]
        assert not configurator._json_has_server(config, "mcpServers")
        data = json.loads(config.read_text(encoding="utf-8"))
        assert "other" in data["mcpServers"]
        again = api.uninstall("fake")
        assert again["ok"] is True and again["already"] is True
    finally:
        configurator.ConfiguratorApi.scan = original_scan
        configurator.AGENT_DEFINITIONS = original_definitions


expected_agents = {
    "codex", "claude", "opencode", "qoder", "qodercn", "codebuddy",
    "pi", "dsh", "cursor", "trae", "vscode", "gemini", "qwen",
}
assert expected_agents <= set(configurator.AGENT_DEFINITIONS)
assert configurator.AGENT_DEFINITIONS["dsh"]["project_supported"] is False
assert configurator.AGENT_DEFINITIONS["qodercn"]["project_supported"] is False
icons_dir = Path(configurator.__file__).resolve().parent / "icons"
for agent_id in expected_agents:
    icon = icons_dir / f"{agent_id}.svg"
    assert icon.is_file(), icon
    assert "<svg" in icon.read_text(encoding="utf-8")
scan = configurator.ConfiguratorApi().scan("")
assert {agent["id"] for agent in scan["agents"]} == expected_agents
assert all("project_supported" in agent and "global_config" in agent for agent in scan["agents"])

print("===== MCP 配置中心辅助测试通过 =====")
