# -*- coding: utf-8 -*-
"""MCP server for read-only access to saved database profiles.

The server intentionally has no dependency on the desktop window or its JS
bridge.  It reads the profile file created by ``app.py`` and exposes only
metadata and bounded SELECT operations over MCP stdio transport.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import decimal
import json
import os
import sys
from pathlib import Path

# Running ``python mcp/server.py`` makes ``mcp/`` the first import path. Add
# the repository root explicitly so this standalone process can reuse dbcore.
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import dbcore


MAX_RETURN_ROWS = 500
DEFAULT_STORE_FILE = Path.home() / ".dbsync_tool" / "connections.json"
PROTOCOL_VERSION = "2024-11-05"


class MCPServerError(Exception):
    """An expected, user-facing MCP tool error."""


def _decode_password(value):
    try:
        return base64.b64decode((value or "").encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def load_saved_profiles(store_file=None):
    """Load profiles without exposing credentials to callers."""
    path = Path(store_file or os.environ.get("DBSYNC_STORE_FILE") or DEFAULT_STORE_FILE)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise MCPServerError("无法读取数据源配置: %s" % exc) from exc
    if not isinstance(raw, list):
        raise MCPServerError("数据源配置格式无效")
    profiles = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        profile = dict(item)
        # Existing app versions persist password_enc. Accepting a plaintext
        # field here is only for backwards compatibility; it is never output.
        if "password_enc" in profile:
            profile["password"] = _decode_password(profile.get("password_enc"))
        profile.pop("password_enc", None)
        profiles.append(profile)
    return profiles


def public_profile(profile):
    """Return a useful profile summary with all credential fields removed."""
    ptype = (profile.get("type") or "").lower()
    allowed = (
        "id", "name", "type", "host", "port", "database", "path",
        "ora_mode", "sid", "service_name", "tag", "user",
    )
    result = {key: profile.get(key) for key in allowed if key in profile}
    result["type_name"] = dbcore.TYPE_NAMES.get(ptype, ptype)
    return result


def _json_value(value):
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _clean_where(where):
    value = (where or "").strip()
    if value.lower().startswith("where "):
        value = value[6:].strip()
    elif value.lower() == "where":
        value = ""
    elif value.lower().startswith("where"):
        value = value[5:].strip()
    if "\x00" in value or "/*" in value or "*/" in value:
        raise MCPServerError("where 条件不允许包含 SQL 注释或控制字符")
    try:
        # Reuse the application's existing semicolon and line-comment checks.
        dbcore.BaseDB._where_sql(value)
    except dbcore.DBError as exc:
        raise MCPServerError(str(exc)) from exc
    return value


def _serialize_col(col):
    return {
        "name": col.name,
        "type": col.type,
        "nullable": bool(col.nullable),
        "default": col.default,
        "primary_key_position": col.pk or None,
        "comment": col.comment or "",
    }


def _serialize_meta(meta, kind):
    result = {
        "kind": kind,
        "name": meta.name,
        "columns": [_serialize_col(col) for col in meta.cols],
        "primary_key": list(meta.pk_cols),
        "comment": meta.table_comment or "",
    }
    if kind == "table":
        result["indexes"] = [
            {"name": idx.name, "columns": list(idx.cols), "unique": bool(idx.unique)}
            for idx in meta.indexes
        ]
        result["unique_constraints"] = [
            {"name": item.name, "columns": list(item.cols), "index_name": item.index_name}
            for item in meta.unique_constraints
        ]
    return result


class ReadOnlyDatabaseService:
    """Database operations used by MCP tools, independent from pywebview."""

    def __init__(self, store_file=None, connector=None):
        self.store_file = Path(store_file) if store_file else None
        self.connector = connector or dbcore.connect

    def profiles(self):
        return load_saved_profiles(self.store_file)

    def public_profiles(self):
        return [public_profile(profile) for profile in self.profiles()]

    def _profile(self, source_id):
        source_id = (source_id or "").strip()
        if not source_id:
            raise MCPServerError("缺少 source_id")
        for profile in self.profiles():
            if str(profile.get("id")) == source_id:
                return profile
        raise MCPServerError("找不到数据源: %s" % source_id)

    def _with_db(self, source_id):
        profile = self._profile(source_id)
        try:
            db = self.connector(profile)
        except Exception as exc:
            raise MCPServerError("连接数据源失败: %s" % exc) from exc
        return profile, db

    @staticmethod
    def _close(db):
        try:
            db.close()
        except Exception:
            pass

    @staticmethod
    def _list_objects_with_db(db, kind):
        if kind not in ("table", "view"):
            raise MCPServerError("不支持的对象类型: %s" % kind)
        if db.dialect == "oracle":
            if kind == "table":
                sql = "SELECT table_name FROM user_tables ORDER BY table_name"
            else:
                sql = "SELECT view_name FROM user_views ORDER BY view_name"
            cur = db.conn.cursor()
            cur.execute(sql)
        elif db.dialect == "mysql":
            object_type = "BASE TABLE" if kind == "table" else "VIEW"
            cur = db.conn.cursor()
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=%s AND table_type=%s ORDER BY table_name",
                (db.schema, object_type),
            )
        else:
            cur = db.conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type=? "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name",
                (kind,),
            )
        try:
            return [row[0] for row in cur.fetchall()]
        finally:
            cur.close()

    def list_objects(self, source_id, kind):
        _profile, db = self._with_db(source_id)
        try:
            return self._list_objects_with_db(db, kind)
        except Exception as exc:
            raise MCPServerError("读取%s列表失败: %s" % ("表" if kind == "table" else "视图", exc)) from exc
        finally:
            self._close(db)

    def _meta(self, source_id, object_name, kind):
        name = (object_name or "").strip()
        try:
            name = dbcore.check_ident(name)
        except dbcore.DBError as exc:
            raise MCPServerError(str(exc)) from exc
        _profile, db = self._with_db(source_id)
        try:
            names = self._list_objects_with_db(db, kind)
            compare_name = name.upper() if db.dialect == "oracle" else name
            known = {str(item).upper() if db.dialect == "oracle" else str(item) for item in names}
            if compare_name not in known:
                raise MCPServerError("数据源中不存在%s: %s" % ("表" if kind == "table" else "视图", name))
            meta = db.table_meta(name)
            if meta is None:
                raise MCPServerError("无法读取%s结构: %s" % ("表" if kind == "table" else "视图", name))
            result = _serialize_meta(meta, kind)
            if kind == "view":
                result["definition"] = self._view_definition(db, name)
            return result
        finally:
            self._close(db)

    @staticmethod
    def _view_definition(db, name):
        cur = db.conn.cursor()
        try:
            if db.dialect == "oracle":
                cur.execute("SELECT text FROM user_views WHERE view_name = :1", [name.upper()])
            elif db.dialect == "mysql":
                cur.execute(
                    "SELECT view_definition FROM information_schema.views "
                    "WHERE table_schema=%s AND table_name=%s", (db.schema, name)
                )
            else:
                cur.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name=?", (name,))
            row = cur.fetchone()
            if not row:
                return ""
            value = db.norm_cell(row[0])
            return "" if value is None else str(value)
        finally:
            cur.close()

    def schema(self, source_id, object_name, kind):
        return self._meta(source_id, object_name, kind)

    def data(self, source_id, object_name, kind, where="", limit=MAX_RETURN_ROWS):
        try:
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise MCPServerError("limit 必须是 1 到 %d 的整数" % MAX_RETURN_ROWS) from exc
        if not 1 <= limit <= MAX_RETURN_ROWS:
            raise MCPServerError("limit 不能超过 %d" % MAX_RETURN_ROWS)
        where = _clean_where(where)
        meta = self._meta(source_id, object_name, kind)
        _profile, db = self._with_db(source_id)
        try:
            columns = [col["name"] for col in meta["columns"]]
            if not columns:
                raise MCPServerError("对象没有可读取的列: %s" % meta["name"])
            select_cols = ", ".join(db.q(column) for column in columns)
            base = "SELECT %s FROM %s" % (select_cols, db.q(meta["name"]))
            where_sql = dbcore.BaseDB._where_sql(where)
            query_limit = limit + 1
            if db.dialect == "oracle":
                sql = "SELECT * FROM (%s%s) WHERE ROWNUM <= %d" % (base, where_sql, query_limit)
            else:
                sql = "%s%s LIMIT %d" % (base, where_sql, query_limit)
            with db.lock:
                cur = db.conn.cursor()
                try:
                    cur.execute(sql)
                    rows = cur.fetchmany(query_limit)
                finally:
                    cur.close()
            truncated = len(rows) > limit
            rows = rows[:limit]
            return {
                "kind": kind,
                "name": meta["name"],
                "columns": columns,
                "rows": [
                    {column: _json_value(db.norm_cell(value)) for column, value in zip(columns, row)}
                    for row in rows
                ],
                "returned_rows": len(rows),
                "limit": limit,
                "truncated": truncated,
            }
        except MCPServerError:
            raise
        except Exception as exc:
            raise MCPServerError("读取%s数据失败: %s" % ("表" if kind == "table" else "视图", exc)) from exc
        finally:
            self._close(db)


TOOL_DEFINITIONS = [
    {
        "name": "list_data_sources",
        "description": "列出桌面工具中已经保存的数据源（不会返回密码）。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_tables",
        "description": "列出指定已保存数据源中的表。",
        "inputSchema": {"type": "object", "required": ["source_id"], "properties": {"source_id": {"type": "string"}}},
    },
    {
        "name": "list_views",
        "description": "列出指定已保存数据源中的视图。",
        "inputSchema": {"type": "object", "required": ["source_id"], "properties": {"source_id": {"type": "string"}}},
    },
    {
        "name": "get_table_schema",
        "description": "读取表结构、主键、索引和列信息。",
        "inputSchema": {"type": "object", "required": ["source_id", "table"], "properties": {"source_id": {"type": "string"}, "table": {"type": "string"}}},
    },
    {
        "name": "get_view_schema",
        "description": "读取视图列结构和视图定义 SQL。",
        "inputSchema": {"type": "object", "required": ["source_id", "view"], "properties": {"source_id": {"type": "string"}, "view": {"type": "string"}}},
    },
    {
        "name": "read_table",
        "description": "读取表数据；单次最多返回 500 行。",
        "inputSchema": {"type": "object", "required": ["source_id", "table"], "properties": {"source_id": {"type": "string"}, "table": {"type": "string"}, "where": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 500}}},
    },
    {
        "name": "read_view",
        "description": "读取视图数据；单次最多返回 500 行。",
        "inputSchema": {"type": "object", "required": ["source_id", "view"], "properties": {"source_id": {"type": "string"}, "view": {"type": "string"}, "where": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 500}}},
    },
]

SERVER_INSTRUCTIONS = (
    "这是一个只读数据库 MCP 服务。使用流程：先调用 list_data_sources，按返回的 name 找到目标数据源的 id；"
    "后续所有工具都把这个 id 作为 source_id 传入。查询表结构调用 get_table_schema，查询视图结构调用 "
    "get_view_schema；读取数据调用 read_table 或 read_view。read_table/read_view 可传 where 和 limit，limit "
    "最大为 500，默认 500。不要猜测 source_id，也不要要求用户提供数据库密码；密码已由服务从已保存配置中使用。"
)

for _tool in TOOL_DEFINITIONS:
    _tool["annotations"] = {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }


def call_tool(service, name, arguments):
    args = arguments if isinstance(arguments, dict) else {}
    if name == "list_data_sources":
        return {"data_sources": service.public_profiles()}
    if name == "list_tables":
        return {"source_id": args.get("source_id"), "tables": service.list_objects(args.get("source_id"), "table")}
    if name == "list_views":
        return {"source_id": args.get("source_id"), "views": service.list_objects(args.get("source_id"), "view")}
    if name == "get_table_schema":
        return service.schema(args.get("source_id"), args.get("table"), "table")
    if name == "get_view_schema":
        return service.schema(args.get("source_id"), args.get("view"), "view")
    if name == "read_table":
        return service.data(args.get("source_id"), args.get("table"), "table", args.get("where", ""), args.get("limit", MAX_RETURN_ROWS))
    if name == "read_view":
        return service.data(args.get("source_id"), args.get("view"), "view", args.get("where", ""), args.get("limit", MAX_RETURN_ROWS))
    raise MCPServerError("未知工具: %s" % name)


def _tool_result(value, is_error=False):
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=_json_value)}], "isError": is_error}


def handle_request(request, service):
    """Handle one JSON-RPC MCP message; return None for notifications."""
    if not isinstance(request, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    request_id = request.get("id")
    method = request.get("method")
    if not method:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600, "message": "Missing method"}}
    if request_id is None and method.startswith("notifications/"):
        return None
    if method == "initialize":
        params = request.get("params") or {}
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "dbsync-readonly", "version": "1.0.0"},
            "instructions": SERVER_INSTRUCTIONS,
        }}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOL_DEFINITIONS}}
    if method == "tools/call":
        params = request.get("params") or {}
        try:
            value = call_tool(service, params.get("name"), params.get("arguments"))
            result = _tool_result(value)
        except Exception as exc:
            result = _tool_result({"error": str(exc)}, is_error=True)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found: %s" % method}}


def run_stdio(service=None, input_stream=None, output_stream=None):
    service = service or ReadOnlyDatabaseService()
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    for line in input_stream:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = handle_request(request, service)
        except Exception as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
            output_stream.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only MCP server for saved database sources")
    parser.add_argument("--store-file", type=Path, help="Override connections.json path (mainly for testing)")
    args = parser.parse_args(argv)
    run_stdio(ReadOnlyDatabaseService(store_file=args.store_file))


if __name__ == "__main__":
    main()
