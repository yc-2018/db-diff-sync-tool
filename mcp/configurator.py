# -*- coding: utf-8 -*-
"""Desktop configuration center for the dbsync MCP server."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SERVER_FILE = Path(__file__).resolve().parent / "server.py"
VENV_PYTHON = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
MCP_NAME = "dbsync"


AGENT_DEFINITIONS = {
    "codex": {"name": "Codex", "command": "codex"},
    "claude": {"name": "Claude Code", "command": "claude"},
    "opencode": {"name": "OpenCode", "command": "opencode"},
}


def _command_path(command):
    found = shutil.which(command)
    if found:
        return found
    appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    localappdata = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    candidates = [
        appdata / "npm" / (command + ".cmd"),
        appdata / "npm" / (command + ".exe"),
    ]
    if command == "codex":
        candidates.append(localappdata / "OpenAI" / "Codex" / "bin" / "codex.exe")
    return next((str(path) for path in candidates if path.is_file()), "")


def _project_path(value):
    value = (value or "").strip()
    if not value:
        return ""
    path = Path(value).expanduser().resolve()
    return str(path) if path.is_dir() else ""


def _json_has_server(path, section):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return False

    servers = value.get(section) if isinstance(value, dict) else None
    return isinstance(servers, dict) and MCP_NAME in servers


def _toml_has_server(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return False
    return bool(re.search(r"(?m)^\s*\[mcp_servers(?:[.]|\s*])", text) and
                re.search(r"(?m)^\s*\[mcp_servers[.]%s(?:[.]|\s*])" % re.escape(MCP_NAME), text))


def _write_codex_project_config(path, command, args):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as exc:
        raise RuntimeError("无法读取 Codex 项目配置: %s" % exc) from exc
    if _toml_has_server(path):
        return False

    def quote(value):
        return json.dumps(str(value), ensure_ascii=False)

    block = "\n\n[mcp_servers.%s]\ncommand = %s\nargs = [%s]\n" % (
        MCP_NAME, quote(command), ", ".join(quote(item) for item in args))
    try:
        path.write_text(text.rstrip() + block, encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("无法写入 Codex 项目配置: %s" % exc) from exc
    return True


def _opencode_global_config():
    appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    candidates = [
        appdata / "opencode" / "opencode.json",
        Path.home() / ".config" / "opencode" / "opencode.json",
    ]
    return next((path for path in candidates if path.is_file()), candidates[0])


def _write_opencode_config(path, command, args):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError) as exc:
        raise RuntimeError("OpenCode 配置不是有效 JSON: %s" % exc) from exc
    if not isinstance(data, dict):
        raise RuntimeError("OpenCode 配置格式无效")
    servers = data.setdefault("mcp", {})
    if not isinstance(servers, dict):
        raise RuntimeError("OpenCode 配置中的 mcp 字段格式无效")
    if MCP_NAME in servers:
        return False
    servers[MCP_NAME] = {"type": "local", "command": [str(command), *[str(item) for item in args]], "enabled": True}
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("无法写入 OpenCode 配置: %s" % exc) from exc
    return True


def _run_cli(binary, args, cwd=None):
    try:
        result = subprocess.run(
            [binary, *args], cwd=cwd or None, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("调用客户端命令失败: %s" % exc) from exc
    if result.returncode:
        message = (result.stderr or result.stdout or "命令失败").strip()
        raise RuntimeError(message)
    return result


class ConfiguratorApi:
    def __init__(self):
        self.window = None

    @staticmethod
    def runtime():
        if getattr(sys, "frozen", False):
            packed_server = Path(sys.executable).resolve().parent.parent / "server" / "dbsync-mcp.exe"
            if packed_server.is_file():
                return str(packed_server), str(packed_server)
        python = VENV_PYTHON if VENV_PYTHON.is_file() else Path(sys.executable).resolve()
        return str(python), str(SERVER_FILE)

    @staticmethod
    def _config_paths(agent_id, project):
        home = Path.home()
        if agent_id == "codex":
            return home / ".codex" / "config.toml", (Path(project) / ".codex" / "config.toml") if project else None
        if agent_id == "claude":
            return home / ".claude.json", (Path(project) / ".mcp.json") if project else None
        return _opencode_global_config(), (Path(project) / "opencode.json") if project else None

    def scan(self, project=""):
        project = _project_path(project)
        python, server = self.runtime()
        result = {"project": project, "python": python, "server": server, "agents": []}
        for agent_id, definition in AGENT_DEFINITIONS.items():
            binary = _command_path(definition["command"])
            global_config, project_config = self._config_paths(agent_id, project)
            if agent_id == "codex":
                global_installed = _toml_has_server(global_config)
                project_installed = bool(project_config and _toml_has_server(project_config))
            else:
                section = "mcpServers" if agent_id == "claude" else "mcp"
                global_installed = _json_has_server(global_config, section)
                project_installed = bool(project_config and _json_has_server(project_config, section))
            result["agents"].append({
                "id": agent_id,
                "name": definition["name"],
                "installed": bool(binary),
                "binary": binary,
                "global_installed": global_installed,
                "project_installed": project_installed,
                "project_selected": bool(project),
                "global_config": str(global_config),
                "project_config": str(project_config) if project_config else "",
            })
        return {"ok": True, **result}

    def choose_project(self):
        try:
            import tkinter
            from tkinter import filedialog
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askdirectory(title="选择项目目录")
            root.destroy()
            return {"ok": True, "path": _project_path(selected)}
        except Exception as exc:
            return {"ok": False, "msg": "无法打开项目选择器: %s" % exc}

    def install(self, agent_id, scope, project=""):
        project = _project_path(project)
        state = self.scan(project)
        agent = next((item for item in state["agents"] if item["id"] == agent_id), None)
        if not agent:
            return {"ok": False, "msg": "未知 agent: %s" % agent_id}
        if not agent["installed"]:
            return {"ok": False, "msg": "%s 未安装或未加入 PATH" % agent["name"]}
        if scope not in ("global", "project"):
            return {"ok": False, "msg": "安装范围无效"}
        if scope == "project" and not project:
            return {"ok": False, "msg": "请先选择项目目录"}
        if scope == "project" and agent["global_installed"]:
            return {"ok": False, "already": True, "msg": "%s 已经全局安装，无需再次项目安装" % agent["name"]}
        if (scope == "global" and agent["global_installed"]) or (scope == "project" and agent["project_installed"]):
            return {"ok": True, "already": True, "msg": "%s 已经安装" % agent["name"]}

        python, server = self.runtime()
        try:
            if agent_id == "codex":
                if scope == "global":
                    _run_cli(agent["binary"], ["mcp", "add", MCP_NAME, "--", python, server])
                else:
                    _write_codex_project_config(agent["project_config"], python, [server])
            elif agent_id == "claude":
                cli_scope = "user" if scope == "global" else "project"
                _run_cli(agent["binary"], ["mcp", "add", "--scope", cli_scope, MCP_NAME, "--", python, server], cwd=project)
            else:
                config = agent["global_config"] if scope == "global" else agent["project_config"]
                _write_opencode_config(config, python, [server])
        except Exception as exc:
            return {"ok": False, "msg": str(exc)}
        return {"ok": True, "already": False, "msg": "%s 已安装" % agent["name"], "state": self.scan(project)}


def main():
    import webview
    api = ConfiguratorApi()
    page = Path(__file__).resolve().parent / "configurator.html"
    window = webview.create_window(
        "MCP 配置中心", str(page), js_api=api, width=1080, height=700,
        min_size=(860, 580), text_select=True, frameless=False,
        easy_drag=False, on_top=False, resizable=True, focus=True)
    api.window = window
    webview.start(debug=False)


if __name__ == "__main__":
    main()
