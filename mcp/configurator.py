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
UNBLOCK_EXTENSIONS = {".dll", ".exe", ".pyd"}


def _home():
    return Path.home()


def _appdata():
    return Path(os.environ.get("APPDATA") or (_home() / "AppData" / "Roaming"))


def _localappdata():
    return Path(os.environ.get("LOCALAPPDATA") or (_home() / "AppData" / "Local"))


def _dsh_home():
    return Path(os.environ.get("DSH_HOME") or (_home() / ".dsh")).expanduser()


def _opencode_global_config():
    candidates = [
        _appdata() / "opencode" / "opencode.json",
        _home() / ".config" / "opencode" / "opencode.json",
    ]
    return next((path for path in candidates if path.is_file()), candidates[0])


def _trae_global_config():
    candidates = [
        _appdata() / "Trae CN" / "User" / "mcp.json",
        _appdata() / "Trae" / "User" / "mcp.json",
    ]
    return next((path for path in candidates if path.is_file()), candidates[0])


def _dsh_patch_files():
    profiles = sorted((_dsh_home() / "profiles").glob("*/cordis.patch.yml"))
    return profiles or [_dsh_home() / "cordis.patch.yml"]


AGENT_DEFINITIONS = {
    "codex": {
        "name": "Codex",
        "commands": ("codex",),
        "kind": "codex",
        "global_config": lambda: _home() / ".codex" / "config.toml",
        "project_config": lambda root: root / ".codex" / "config.toml",
        "detect_paths": lambda: [_home() / ".codex"],
    },
    "claude": {
        "name": "Claude Code",
        "commands": ("claude",),
        "kind": "claude",
        "global_config": lambda: _home() / ".claude.json",
        "project_config": lambda root: root / ".mcp.json",
        "detect_paths": lambda: [_home() / ".claude.json", _home() / ".claude"],
    },
    "opencode": {
        "name": "OpenCode",
        "commands": ("opencode",),
        "kind": "opencode",
        "global_config": _opencode_global_config,
        "project_config": lambda root: root / "opencode.json",
        "detect_paths": lambda: [
            _appdata() / "opencode",
            _home() / ".config" / "opencode",
        ],
    },
    "qoder": {
        "name": "Qoder",
        "commands": ("qoder",),
        "kind": "json",
        "section": "mcpServers",
        "global_config": lambda: _home() / ".qoder" / "settings.json",
        "project_config": lambda root: root / ".qoder" / "settings.json",
        "detect_paths": lambda: [_home() / ".qoder"],
    },
    "qodercn": {
        "name": "Qoder CN",
        "commands": ("qodercn",),
        "kind": "json",
        "section": "mcpServers",
        "global_config": lambda: _home() / ".qoder-cn" / "settings.json",
        "project_config": None,
        "project_supported": False,
        "detect_paths": lambda: [_home() / ".qoder-cn"],
    },
    "codebuddy": {
        "name": "WorkBuddy / CodeBuddy",
        "commands": ("codebuddy", "workbuddy"),
        "kind": "json",
        "section": "mcpServers",
        "global_config": lambda: _home() / ".codebuddy" / ".mcp.json",
        "project_config": lambda root: root / ".mcp.json",
        "detect_paths": lambda: [_home() / ".codebuddy"],
    },
    "pi": {
        "name": "Pi",
        "commands": ("pi",),
        "kind": "json",
        "section": "mcpServers",
        "global_config": lambda: _home() / ".pi" / "agent" / "mcp.json",
        "project_config": lambda root: root / ".pi" / "mcp.json",
        "detect_paths": lambda: [_home() / ".pi"],
    },
    "dsh": {
        "name": "DeepSeek Harness (dsh)",
        "commands": ("dsh",),
        "kind": "dsh",
        "global_config": lambda: _dsh_patch_files()[0],
        "project_config": None,
        "project_supported": False,
        "detect_paths": lambda: [_dsh_home()],
    },
    "cursor": {
        "name": "Cursor",
        "commands": ("cursor",),
        "kind": "json",
        "section": "mcpServers",
        "global_config": lambda: _home() / ".cursor" / "mcp.json",
        "project_config": lambda root: root / ".cursor" / "mcp.json",
        "detect_paths": lambda: [_home() / ".cursor"],
    },
    "trae": {
        "name": "Trae",
        "commands": ("trae",),
        "kind": "json",
        "section": "mcpServers",
        "global_config": _trae_global_config,
        "project_config": lambda root: root / ".trae" / "mcp.json",
        "detect_paths": lambda: [
            _appdata() / "Trae CN",
            _appdata() / "Trae",
            _home() / ".trae",
            _home() / ".trae-cn",
        ],
    },
    "vscode": {
        "name": "VS Code Copilot",
        "commands": ("code", "code-insiders"),
        "kind": "json",
        "section": "mcpServers",
        "global_config": lambda: _home() / ".copilot" / "mcp-config.json",
        "project_config": lambda root: root / ".mcp.json",
        "detect_paths": lambda: [
            _appdata() / "Code",
            _home() / ".copilot",
        ],
    },
    "gemini": {
        "name": "Gemini CLI",
        "commands": ("gemini",),
        "kind": "json",
        "section": "mcpServers",
        "global_config": lambda: _home() / ".gemini" / "settings.json",
        "project_config": lambda root: root / ".gemini" / "settings.json",
        "detect_paths": lambda: [_home() / ".gemini"],
    },
    "qwen": {
        "name": "Qwen Code",
        "commands": ("qwen",),
        "kind": "json",
        "section": "mcpServers",
        "global_config": lambda: _home() / ".qwen" / "settings.json",
        "project_config": lambda root: root / ".qwen" / "settings.json",
        "detect_paths": lambda: [_home() / ".qwen"],
    },
}


def unblock_bundled_runtime_files():
    """Remove Windows Internet Zone marks from downloaded frozen runtimes."""
    if not getattr(sys, "frozen", False):
        return
    try:
        root = Path(sys.executable).resolve().parent
    except OSError:
        return
    try:
        for base in (root, root / "_internal"):
            if not base.is_dir():
                continue
            for path in base.rglob("*"):
                if path.suffix.lower() not in UNBLOCK_EXTENSIONS:
                    continue
                try:
                    os.remove(str(path) + ":Zone.Identifier")
                except OSError:
                    pass
    except OSError:
        pass


def resource_path(name):
    """Resolve a configurator asset in source and PyInstaller runtimes."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root) / "mcp" / name
    return Path(__file__).resolve().parent / name


def _command_path(command):
    found = shutil.which(command)
    if found:
        return found
    appdata = _appdata()
    candidates = [
        appdata / "npm" / (command + ".cmd"),
        appdata / "npm" / (command + ".exe"),
    ]
    if command == "codex":
        candidates.append(_localappdata() / "OpenAI" / "Codex" / "bin" / "codex.exe")
    return next((str(path) for path in candidates if path.is_file()), "")


def _project_path(value):
    value = (value or "").strip()
    if not value:
        return ""
    path = Path(value).expanduser().resolve()
    return str(path) if path.is_dir() else ""


def _json_has_server(path, section):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
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


def _is_codex_server_header(line):
    pattern = r"^\s*\[\s*mcp_servers\s*\.\s*(?:%s|\"%s\"|'%s')\s*(?:\.[^\]]*)?\]" % (
        re.escape(MCP_NAME), re.escape(MCP_NAME), re.escape(MCP_NAME))
    return bool(re.match(pattern, line))


def _remove_codex_server_config(path):
    path = Path(path)
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("无法读取 Codex 配置: %s" % exc) from exc
    lines = text.splitlines(keepends=True)
    kept = []
    skipping = False
    changed = False
    for line in lines:
        if line.lstrip().startswith("["):
            if _is_codex_server_header(line):
                skipping = True
                changed = True
                continue
            skipping = False
        if not skipping:
            kept.append(line)
    if not changed:
        return False
    try:
        path.write_text("".join(kept), encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("无法写入 Codex 配置: %s" % exc) from exc
    return True


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


def _write_opencode_config(path, command, args):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
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


def _write_json_mcp_config(path, entry, section="mcpServers"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
        data = json.loads(text) if text.strip() else {}
    except (OSError, ValueError) as exc:
        raise RuntimeError("配置文件不是有效 JSON: %s" % exc) from exc
    if not isinstance(data, dict):
        raise RuntimeError("配置文件格式无效")
    servers = data.setdefault(section, {})
    if not isinstance(servers, dict):
        raise RuntimeError("配置中的 %s 字段格式无效" % section)
    if MCP_NAME in servers:
        return False
    servers[MCP_NAME] = entry
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("无法写入配置文件: %s" % exc) from exc
    return True


def _remove_json_mcp_config(path, section="mcpServers"):
    path = Path(path)
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8-sig")
        data = json.loads(text) if text.strip() else {}
    except (OSError, ValueError) as exc:
        raise RuntimeError("配置文件不是有效 JSON: %s" % exc) from exc
    if not isinstance(data, dict):
        raise RuntimeError("配置文件格式无效")
    servers = data.get(section)
    if not isinstance(servers, dict) or MCP_NAME not in servers:
        return False
    del servers[MCP_NAME]
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("无法写入配置文件: %s" % exc) from exc
    return True


def _dsh_has_server_text(text):
    name_pattern = r"(?m)^\s*serverName:\s*[\"']?%s[\"']?\s*$" % re.escape(MCP_NAME)
    id_pattern = r"(?m)^\s*-\s*id:\s*[\"']?mcp-%s[\"']?\s*$" % re.escape(MCP_NAME)
    return bool(re.search(name_pattern, text) or re.search(id_pattern, text))


def _dsh_insert_entry(text, command, args):
    entry = [
        "- id: mcp-%s" % MCP_NAME,
        "  name: \"@deepseek-ai/dsh-mcp-client\"",
        "  config:",
        "    serverName: %s" % MCP_NAME,
        "    transport: stdio",
        "    command: %s" % json.dumps(str(command), ensure_ascii=False),
        "    args:",
    ]
    entry.extend("      - %s" % json.dumps(str(item), ensure_ascii=False) for item in args)

    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"^-\s*insert:\s*(#.*)?$", line):
            continue
        end = len(lines)
        for cursor in range(index + 1, len(lines)):
            if re.match(r"^-(?:\s|$)", lines[cursor]):
                end = cursor
                break
        item_indent = "    "
        for cursor in range(index + 1, end):
            match = re.match(r"^(\s+)-\s", lines[cursor])
            if match:
                item_indent = match.group(1)
                break
        position = end
        while position > index + 1 and (
                not lines[position - 1].strip() or lines[position - 1].lstrip().startswith("#")):
            position -= 1
        lines[position:position] = [item_indent + item for item in entry]
        result = "\n".join(lines)
        return result + ("\n" if text.endswith("\n") else "")

    block = ["- insert:"] + ["    " + item for item in entry]
    prefix = text.rstrip()
    return (prefix + "\n\n" if prefix else "") + "\n".join(block) + "\n"


def _write_dsh_config(command, args):
    changed = False
    for path in _dsh_patch_files():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
        except OSError as exc:
            raise RuntimeError("无法读取 DSH 配置: %s" % exc) from exc
        if _dsh_has_server_text(text):
            continue
        try:
            path.write_text(_dsh_insert_entry(text, command, args), encoding="utf-8")
        except OSError as exc:
            raise RuntimeError("无法写入 DSH 配置: %s" % exc) from exc
        changed = True
    return changed


def _dsh_item_bounds(lines, start, indent):
    end = start + 1
    content_end = end
    while end < len(lines):
        line = lines[end]
        if not line.strip():
            end += 1
            continue
        stripped = line.lstrip()
        line_indent = len(line) - len(stripped)
        if stripped.startswith("#") and line_indent <= indent:
            break
        item = re.match(r"^(\s*)-\s", line)
        if item and len(item.group(1)) <= indent:
            break
        if line_indent <= indent and not item:
            break
        end += 1
        content_end = end
    return start, content_end


def _dsh_remove_one_entry(lines):
    candidates = []
    for index, line in enumerate(lines):
        item = re.match(r"^(\s*)-\s", line)
        if not item:
            continue
        indent = len(item.group(1))
        start, end = _dsh_item_bounds(lines, index, indent)
        if _dsh_has_server_text("".join(lines[start:end])):
            candidates.append((indent, index, start, end))
    if not candidates:
        return False
    _, _, start, end = max(candidates, key=lambda value: (value[0], value[1]))
    del lines[start:end]
    return True


def _dsh_remove_empty_inserts(lines):
    changed = False
    index = 0
    while index < len(lines):
        item = re.match(r"^(\s*)-\s*insert:\s*(?:#.*)?$", lines[index])
        if not item:
            index += 1
            continue
        indent = len(item.group(1))
        cursor = index + 1
        has_content = False
        while cursor < len(lines):
            if not lines[cursor].strip():
                cursor += 1
                continue
            if len(lines[cursor]) - len(lines[cursor].lstrip()) <= indent:
                break
            has_content = True
            break
        if has_content:
            index += 1
            continue
        end = index + 1
        while end < len(lines) and not lines[end].strip():
            end += 1
        del lines[index:end]
        changed = True
    return changed


def _dsh_remove_entries(text):
    lines = text.splitlines(keepends=True)
    changed = False
    while _dsh_remove_one_entry(lines):
        changed = True
    if changed:
        _dsh_remove_empty_inserts(lines)
    return "".join(lines), changed


def _remove_dsh_config():
    changed = False
    for path in _dsh_patch_files():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise RuntimeError("无法读取 DSH 配置: %s" % exc) from exc
        updated, removed = _dsh_remove_entries(text)
        if not removed:
            continue
        try:
            path.write_text(updated, encoding="utf-8")
        except OSError as exc:
            raise RuntimeError("无法写入 DSH 配置: %s" % exc) from exc
        changed = True
    return changed


def _dsh_installed():
    for path in _dsh_patch_files():
        try:
            if path.is_file() and _dsh_has_server_text(path.read_text(encoding="utf-8-sig", errors="replace")):
                return True
        except OSError:
            continue
    return False


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
        definition = AGENT_DEFINITIONS[agent_id]
        global_config = definition["global_config"]()
        project_builder = definition.get("project_config")
        project_config = project_builder(Path(project)) if project and project_builder else None
        return global_config, project_config

    def scan(self, project=""):
        project = _project_path(project)
        python, server = self.runtime()
        result = {"project": project, "python": python, "server": server, "agents": []}
        for agent_id, definition in AGENT_DEFINITIONS.items():
            binary = next((path for path in (_command_path(name) for name in definition["commands"]) if path), "")
            detected = bool(binary) or any(path.exists() for path in definition["detect_paths"]())
            global_config, project_config = self._config_paths(agent_id, project)
            if agent_id == "codex":
                global_installed = _toml_has_server(global_config)
                project_installed = bool(project_config and _toml_has_server(project_config))
            elif agent_id == "dsh":
                global_installed = _dsh_installed()
                project_installed = False
            else:
                section = definition.get("section", "mcpServers")
                global_installed = _json_has_server(global_config, section)
                project_installed = bool(project_config and _json_has_server(project_config, section))
            result["agents"].append({
                "id": agent_id,
                "name": definition["name"],
                "installed": detected,
                "binary": binary,
                "global_installed": global_installed,
                "project_installed": project_installed,
                "project_supported": definition.get("project_supported", True),
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
            return {"ok": False, "msg": "%s 未安装或未检测到配置目录" % agent["name"]}
        if scope not in ("global", "project"):
            return {"ok": False, "msg": "安装范围无效"}
        if scope == "project" and not agent["project_supported"]:
            return {"ok": False, "msg": "%s 仅支持全局安装" % agent["name"]}
        if scope == "project" and not project:
            return {"ok": False, "msg": "请先选择项目目录"}
        if scope == "project" and agent["global_installed"]:
            return {"ok": False, "already": True, "msg": "%s 已经全局安装，无需再次项目安装" % agent["name"]}
        if (scope == "global" and agent["global_installed"]) or (scope == "project" and agent["project_installed"]):
            return {"ok": True, "already": True, "msg": "%s 已经安装" % agent["name"]}

        definition = AGENT_DEFINITIONS[agent_id]
        python, server = self.runtime()
        try:
            if definition["kind"] == "codex":
                if scope == "global" and agent["binary"]:
                    _run_cli(agent["binary"], ["mcp", "add", MCP_NAME, "--", python, server])
                else:
                    _write_codex_project_config(
                        agent["global_config"] if scope == "global" else agent["project_config"],
                        python, [server])
            elif definition["kind"] == "claude":
                if not agent["binary"]:
                    return {"ok": False, "msg": "Claude Code 未安装或未加入 PATH"}
                cli_scope = "user" if scope == "global" else "project"
                _run_cli(agent["binary"], ["mcp", "add", "--scope", cli_scope, MCP_NAME, "--", python, server], cwd=project)
            elif definition["kind"] == "opencode":
                config = agent["global_config"] if scope == "global" else agent["project_config"]
                _write_opencode_config(config, python, [server])
            elif definition["kind"] == "dsh":
                _write_dsh_config(python, [server])
            else:
                config = agent["global_config"] if scope == "global" else agent["project_config"]
                entry = {"command": python, "args": [server]}
                if agent_id in ("codebuddy", "vscode"):
                    entry = {"type": "stdio", **entry}
                _write_json_mcp_config(config, entry, definition.get("section", "mcpServers"))
        except Exception as exc:
            return {"ok": False, "msg": str(exc)}
        return {"ok": True, "already": False, "msg": "%s 已安装" % agent["name"], "state": self.scan(project)}

    def uninstall(self, agent_id, scope="global", project=""):
        project = _project_path(project)
        state = self.scan(project)
        agent = next((item for item in state["agents"] if item["id"] == agent_id), None)
        if not agent:
            return {"ok": False, "msg": "未知 agent: %s" % agent_id}
        if scope != "global":
            return {"ok": False, "msg": "目前仅支持关闭全局配置"}
        if not agent["global_installed"]:
            return {"ok": True, "already": True, "msg": "%s 全局配置未安装" % agent["name"], "state": state}

        definition = AGENT_DEFINITIONS[agent_id]
        try:
            kind = definition["kind"]
            if kind == "claude":
                if agent["binary"]:
                    _run_cli(agent["binary"], ["mcp", "remove", "--scope", "user", MCP_NAME])
                elif not _remove_json_mcp_config(agent["global_config"]):
                    return {"ok": False, "msg": "未找到 %s 的 dbsync 全局配置" % agent["name"]}
            elif kind == "codex":
                if not _remove_codex_server_config(agent["global_config"]):
                    return {"ok": False, "msg": "未找到 %s 的 dbsync 全局配置" % agent["name"]}
            elif kind == "dsh":
                if not _remove_dsh_config():
                    return {"ok": False, "msg": "未找到 %s 的 dbsync 全局配置" % agent["name"]}
            elif kind == "opencode":
                if not _remove_json_mcp_config(agent["global_config"], "mcp"):
                    return {"ok": False, "msg": "未找到 %s 的 dbsync 全局配置" % agent["name"]}
            else:
                section = definition.get("section", "mcpServers")
                if not _remove_json_mcp_config(agent["global_config"], section):
                    return {"ok": False, "msg": "未找到 %s 的 dbsync 全局配置" % agent["name"]}
        except Exception as exc:
            return {"ok": False, "msg": str(exc)}
        return {"ok": True, "already": False, "msg": "%s 已关闭全局配置" % agent["name"], "state": self.scan(project)}


def main():
    unblock_bundled_runtime_files()
    import webview
    api = ConfiguratorApi()
    page = resource_path("configurator.html")
    window = webview.create_window(
        "MCP 配置中心", str(page), js_api=api, width=1080, height=700,
        min_size=(860, 580), text_select=True, frameless=False,
        easy_drag=False, on_top=False, resizable=True, focus=True)
    webview.start(debug=False)


if __name__ == "__main__":
    main()
