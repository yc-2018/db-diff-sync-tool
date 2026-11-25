# -*- coding: utf-8 -*-
"""MCP server for database access to saved database profiles.

The server intentionally has no dependency on the desktop window or its JS
bridge. It reads the profile file created by ``app.py`` and exposes metadata,
bounded SELECT operations, and a guarded ``copy_table_to_test`` data copy tool
that can only write to a target profile tagged ``test``.
"""
from __future__ import annotations

import argparse
import base64
import copy
import datetime
import decimal
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Running ``python mcp/server.py`` makes ``mcp/`` the first import path. Add
# the repository root explicitly so this standalone process can reuse dbcore.
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import dbcore


MAX_RETURN_ROWS = 500
MAX_BATCH_OBJECTS = 20
DEFAULT_SCHEMA_TTL = 60
DEFAULT_STORE_FILE = Path.home() / ".dbsync_tool" / "connections.json"
AI_WRITE_AUDIT_FILE = Path.home() / ".dbsync_tool" / "ai_write_audit.log"
PROTOCOL_VERSION = "2024-11-05"
MCP_SERVER_VERSION = "2.0.24"

_QUERY_FORBIDDEN = re.compile(
    r"\b(?:insert|update|delete|merge|drop|alter|truncate|create|grant|revoke|"
    r"call|exec|execute|into|for\s+update)\b",
    re.IGNORECASE,
)
_QUERY_START = re.compile(r"^\s*(?:select|with)\b", re.IGNORECASE)
_WRITE_START = re.compile(r"^\s*(?:insert|update|delete)\b", re.IGNORECASE)
_WRITE_FORBIDDEN = re.compile(
    # 注意：不能使用裸 "into"，否则会误杀所有 INSERT INTO；
    # 只拦截写入文件危险的 INTO OUTFILE / INTO DUMPFILE。
    r"\b(?:select|with|merge|drop|alter|truncate|create|grant|revoke|call|exec|"
    r"execute|into\s+(?:outfile|dumpfile)|for\s+update)\b",
    re.IGNORECASE,
)


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
        "ora_mode", "sid", "service_name", "tag", "user", "allow_ai_write",
    )
    result = {key: profile.get(key) for key in allowed if key in profile}
    result["type_name"] = dbcore.TYPE_NAMES.get(ptype, ptype)
    result["is_test"] = profile.get("tag") == "test"
    result["allow_ai_write"] = bool(profile.get("allow_ai_write")) and result["is_test"]
    return result


def _json_value(value):
    """Recursively make database values JSON serializable.

    MySQL JSON columns may come back as dict/list, and earlier versions used
    ``str()`` which produced Python repr instead of valid JSON.
    """
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
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
    if "\x00" in value or "/*" in value or "*/" in value or "#" in value:
        raise MCPServerError("where 条件不允许包含 SQL 注释或控制字符")
    try:
        # Reuse the application's existing semicolon and line-comment checks.
        dbcore.BaseDB._where_sql(value)
    except dbcore.DBError as exc:
        raise MCPServerError(str(exc)) from exc
    return value


def _clean_query(sql):
    """Reject anything that is not a single read-only SELECT/CTE."""
    value = (sql or "").strip()
    if not value:
        raise MCPServerError("缺少 sql")
    if (";" in value or "--" in value or "/*" in value or "*/" in value
            or "\x00" in value or "#" in value):
        raise MCPServerError("query 不允许包含分号、注释或控制字符")
    if not _QUERY_START.match(value):
        raise MCPServerError("query 只允许 SELECT 或 WITH 查询")
    if _QUERY_FORBIDDEN.search(value):
        raise MCPServerError("query 只读，不允许包含写入、DDL 或锁表关键字")
    return value


def _clean_dml(sql):
    """Validate a single INSERT/UPDATE/DELETE statement for test-only writes."""
    value = (sql or "").strip()
    if not value:
        raise MCPServerError("缺少 sql")
    if (";" in value or "--" in value or "/*" in value or "*/" in value
            or "\x00" in value or "#" in value):
        raise MCPServerError("写 SQL 不允许包含分号、注释或控制字符")
    if not _WRITE_START.match(value):
        raise MCPServerError("只允许 INSERT、UPDATE、DELETE 三种 DML")
    if _WRITE_FORBIDDEN.search(value):
        raise MCPServerError("写 SQL 不允许包含查询、DDL 或锁表关键字")
    return value


def _parse_limit(value, default=MAX_RETURN_ROWS):
    try:
        limit = int(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise MCPServerError("limit 必须是 1 到 %d 的整数" % MAX_RETURN_ROWS) from exc
    if not 1 <= limit <= MAX_RETURN_ROWS:
        raise MCPServerError("limit 必须是 1 到 %d 的整数" % MAX_RETURN_ROWS)
    return limit


def _parse_offset(value):
    try:
        offset = int(0 if value is None else value)
    except (TypeError, ValueError) as exc:
        raise MCPServerError("offset 必须是不小于 0 的整数") from exc
    if offset < 0:
        raise MCPServerError("offset 必须是不小于 0 的整数")
    return offset


def _parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise MCPServerError("count_only 必须是布尔值")


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
    """Database operations used by MCP tools, independent from pywebview.

    Connections are kept per source for the lifetime of the MCP process.  The
    previous implementation opened a new connection for every tool call (and
    two connections for every ``read_table``), which is especially expensive
    for Oracle.
    """

    def __init__(self, store_file=None, connector=None, schema_ttl=DEFAULT_SCHEMA_TTL):
        self.store_file = Path(store_file) if store_file else None
        self.connector = connector or dbcore.connect
        self.schema_ttl = schema_ttl
        self.audit_file = (
            (Path(store_file).parent / "ai_write_audit.log")
            if store_file else AI_WRITE_AUDIT_FILE
        )
        self._lock = threading.RLock()
        self._profiles_cache = None
        self._profiles_mtime = None
        self._db_cache = {}
        self._schema_cache = {}
        self._object_cache = {}

    # ------------------------------------------------------------ profiles
    def _store_path(self):
        return Path(self.store_file or os.environ.get("DBSYNC_STORE_FILE") or DEFAULT_STORE_FILE)

    def profiles(self):
        path = self._store_path()
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = None
        with self._lock:
            if self._profiles_cache is not None and mtime == self._profiles_mtime:
                return [copy.deepcopy(item) for item in self._profiles_cache]
            profiles = load_saved_profiles(path)
            self._profiles_cache = profiles
            self._profiles_mtime = mtime
            # A changed profile set can point to different databases.
            self._close_all_locked()
            return [copy.deepcopy(item) for item in profiles]

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

    # ------------------------------------------------------------ connections
    def _with_db(self, source_id, profile=None):
        source_id = (source_id or "").strip()
        profile = profile or self._profile(source_id)
        with self._lock:
            db = self._db_cache.get(source_id)
            if db is not None:
                return profile, db
            try:
                db = self.connector(profile)
            except Exception as exc:
                raise MCPServerError("连接数据源失败: %s" % exc) from exc
            self._db_cache[source_id] = db
            return profile, db

    def _forget_db(self, source_id):
        with self._lock:
            db = self._db_cache.pop(source_id, None)
            if db is not None:
                self._close(db)

    @staticmethod
    def _close(db):
        try:
            db.close()
        except Exception:
            pass

    def _close_all_locked(self):
        for db in self._db_cache.values():
            self._close(db)
        self._db_cache.clear()
        self._schema_cache.clear()
        self._object_cache.clear()

    def close_all(self):
        with self._lock:
            self._close_all_locked()

    def close_source(self, source_id):
        with self._lock:
            db = self._db_cache.pop((source_id or "").strip(), None)
            if db is not None:
                self._close(db)
            for key in list(self._schema_cache):
                if key[0] == (source_id or "").strip():
                    self._schema_cache.pop(key, None)
            for key in list(self._object_cache):
                if key[0] == (source_id or "").strip():
                    self._object_cache.pop(key, None)

    # ------------------------------------------------------------ metadata
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

    def _object_names(self, source_id, kind):
        key = ((source_id or "").strip(), kind)
        now = time.monotonic()
        with self._lock:
            cached = self._object_cache.get(key)
            if cached and now - cached[0] < self.schema_ttl:
                return list(cached[1])
        _profile, db = self._with_db(source_id)
        try:
            names = self._list_objects_with_db(db, kind)
        except Exception as exc:
            raise MCPServerError("读取%s列表失败: %s" % ("表" if kind == "table" else "视图", exc)) from exc
        with self._lock:
            self._object_cache[key] = (now, list(names))
        return names

    def list_objects(self, source_id, kind, name_filter=None, limit=0, offset=0):
        try:
            limit = int(0 if limit is None else limit)
            offset = int(0 if offset is None else offset)
        except (TypeError, ValueError) as exc:
            raise MCPServerError("limit/offset 必须是整数") from exc
        if limit < 0 or offset < 0:
            raise MCPServerError("limit/offset 不能小于 0")
        names = self._object_names(source_id, kind)
        if name_filter:
            needle = str(name_filter).casefold()
            names = [name for name in names if needle in str(name).casefold()]
        if offset:
            names = names[offset:]
        if limit:
            names = names[:limit]
        return names

    def _schema_with_db(self, source_id, db, object_name, kind):
        name = dbcore.check_ident((object_name or "").strip())
        cache_key = ((source_id or "").strip(), kind,
                     name.upper() if db.dialect == "oracle" else name)
        now = time.monotonic()
        with self._lock:
            cached = self._schema_cache.get(cache_key)
            if cached and now - cached[0] < self.schema_ttl:
                return copy.deepcopy(cached[1])
        meta = db.table_meta(name)
        if meta is None:
            raise MCPServerError("数据源中不存在%s: %s" % ("表" if kind == "table" else "视图", name))
        result = _serialize_meta(meta, kind)
        if kind == "view":
            result["definition"] = self._view_definition(db, name)
        with self._lock:
            self._schema_cache[cache_key] = (now, copy.deepcopy(result))
        return result

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
        _profile, db = self._with_db(source_id)
        try:
            return self._schema_with_db(source_id, db, object_name, kind)
        except MCPServerError:
            raise
        except Exception as exc:
            raise MCPServerError("读取%s结构失败: %s" % ("表" if kind == "table" else "视图", exc)) from exc

    def schema_many(self, source_id, objects, kind):
        objects = objects or []
        if not isinstance(objects, list) or not objects:
            raise MCPServerError("objects 必须是非空数组")
        if len(objects) > MAX_BATCH_OBJECTS:
            raise MCPServerError("单次最多查询 %d 个对象的结构" % MAX_BATCH_OBJECTS)
        results, errors = [], []
        _profile, db = self._with_db(source_id)
        for object_name in objects:
            try:
                item = self._schema_with_db(source_id, db, object_name, kind)
                results.append(item)
            except Exception as exc:
                errors.append({"name": object_name, "error": str(exc)})
        return {"source_id": source_id, "schemas": results, "errors": errors}

    # ------------------------------------------------------------ data reading
    @staticmethod
    def _resolve_column(columns, requested, dialect):
        name = dbcore.check_ident((requested or "").strip())
        lookup = {col.upper(): col for col in columns} if dialect == "oracle" else {col: col for col in columns}
        key = name.upper() if dialect == "oracle" else name
        if key not in lookup:
            raise MCPServerError("列不存在: %s" % requested)
        return lookup[key]

    def _select_columns(self, meta, columns, dialect):
        all_columns = [col["name"] for col in meta["columns"]]
        if not all_columns:
            raise MCPServerError("对象没有可读取的列: %s" % meta["name"])
        if columns is None:
            return all_columns
        if isinstance(columns, str):
            columns = [part.strip() for part in columns.split(",") if part.strip()]
        if not isinstance(columns, (list, tuple)) or not columns:
            raise MCPServerError("columns 必须是非空字符串数组")
        return [self._resolve_column(all_columns, column, dialect) for column in columns]

    def _order_sql(self, db, meta, order_by):
        if order_by:
            parts = [part.strip() for part in str(order_by).split(",") if part.strip()]
            if not parts:
                return ""
            values = []
            for part in parts:
                tokens = part.split()
                if len(tokens) > 2:
                    raise MCPServerError("order_by 格式错误: %s" % part)
                column = self._resolve_column([col["name"] for col in meta["columns"]], tokens[0], db.dialect)
                direction = (tokens[1] if len(tokens) == 2 else "ASC").upper()
                if direction not in ("ASC", "DESC"):
                    raise MCPServerError("order_by 排序方向只能是 ASC/DESC: %s" % part)
                values.append("%s %s" % (db.q(column), direction))
            return " ORDER BY " + ", ".join(values)
        primary_key = meta.get("primary_key") or []
        if primary_key:
            return " ORDER BY " + ", ".join(db.q(column) for column in primary_key)
        return ""

    def _execute_rows(self, db, sql, limit, offset):
        """Return at most ``limit + 1`` rows for the caller to detect truncation."""
        with db.lock:
            cur = db.conn.cursor()
            try:
                cur.execute(sql)
                return cur.fetchmany(limit + 1)
            finally:
                cur.close()

    def data(self, source_id, object_name, kind, where="", limit=MAX_RETURN_ROWS,
             columns=None, offset=0, order_by="", count_only=False):
        limit = _parse_limit(limit)
        offset = _parse_offset(offset)
        where = _clean_where(where)
        meta = self.schema(source_id, object_name, kind)
        _profile, db = self._with_db(source_id)
        try:
            if count_only:
                table_q = db.q(meta["name"])
                where_sql = dbcore.BaseDB._where_sql(where)
                sql = "SELECT COUNT(*) FROM %s%s" % (table_q, where_sql)
                with db.lock:
                    cur = db.conn.cursor()
                    try:
                        cur.execute(sql)
                        row = cur.fetchone()
                    finally:
                        cur.close()
                return {
                    "kind": kind,
                    "name": meta["name"],
                    "count_only": True,
                    "row_count": int(row[0] or 0),
                    "where": where,
                }

            selected_columns = self._select_columns(meta, columns, db.dialect)
            if not selected_columns:
                raise MCPServerError("对象没有可读取的列: %s" % meta["name"])
            select_cols = ", ".join(db.q(column) for column in selected_columns)
            base = "SELECT %s FROM %s" % (select_cols, db.q(meta["name"]))
            where_sql = dbcore.BaseDB._where_sql(where)
            order_sql = self._order_sql(db, meta, order_by)
            query_limit = limit + 1

            if db.dialect == "oracle":
                if offset:
                    inner = "%s%s%s" % (base, where_sql, order_sql)
                    outer_cols = ", ".join(db.q(column) for column in selected_columns)
                    alias = "DBSYNC_RN"
                    sql = (
                        "SELECT %s FROM ("
                        "SELECT %s, ROWNUM AS %s FROM (%s) WHERE ROWNUM <= %d"
                        ") WHERE %s > %d"
                    ) % (outer_cols, outer_cols, alias, inner, offset + query_limit, alias, offset)
                else:
                    sql = "SELECT * FROM (%s%s%s) WHERE ROWNUM <= %d" % (
                        base, where_sql, order_sql, query_limit)
            else:
                sql = "%s%s%s LIMIT %d OFFSET %d" % (
                    base, where_sql, order_sql, query_limit, offset)

            rows = self._execute_rows(db, sql, limit, offset)
            truncated = len(rows) > limit
            rows = rows[:limit]
            result = {
                "kind": kind,
                "name": meta["name"],
                "columns": selected_columns,
                "rows": [
                    {column: _json_value(db.norm_cell(value)) for column, value in zip(selected_columns, row)}
                    for row in rows
                ],
                "returned_rows": len(rows),
                "limit": limit,
                "offset": offset,
                "truncated": truncated,
                "has_more": truncated,
                "next_offset": offset + len(rows) if truncated else None,
            }
            return result
        except MCPServerError:
            raise
        except Exception as exc:
            # A dead cached connection should not poison later calls.
            self._forget_db(source_id)
            raise MCPServerError("读取%s数据失败: %s" % ("表" if kind == "table" else "视图", exc)) from exc

    def read_tables(self, source_id, requests):
        if not isinstance(requests, list) or not requests:
            raise MCPServerError("requests 必须是非空数组")
        if len(requests) > MAX_BATCH_OBJECTS:
            raise MCPServerError("单次最多读取 %d 张表" % MAX_BATCH_OBJECTS)
        results = []
        for request in requests:
            if not isinstance(request, dict):
                results.append({"error": "请求项必须是对象"})
                continue
            table = request.get("table")
            try:
                item = self.data(
                    source_id,
                    table,
                    "table",
                    where=request.get("where", ""),
                    limit=request.get("limit", MAX_RETURN_ROWS),
                    columns=request.get("columns"),
                    offset=request.get("offset", 0),
                    order_by=request.get("order_by", ""),
                    count_only=request.get("count_only", False),
                )
                item["table"] = table
                results.append(item)
            except Exception as exc:
                results.append({"table": table, "error": str(exc)})
        return {"source_id": source_id, "results": results}

    # ------------------------------------------------------------ safe query
    def query(self, source_id, sql, params=None, limit=MAX_RETURN_ROWS):
        sql = _clean_query(sql)
        limit = _parse_limit(limit)
        if params is None:
            params = []
        if not isinstance(params, (list, tuple)):
            raise MCPServerError("params 必须是数组")
        _profile, db = self._with_db(source_id)
        try:
            with db.lock:
                cur = db.conn.cursor()
                try:
                    if params:
                        cur.execute(sql, list(params))
                    else:
                        cur.execute(sql)
                    description = cur.description or []
                    columns = [item[0] for item in description]
                    rows = cur.fetchmany(limit + 1)
                finally:
                    cur.close()
            truncated = len(rows) > limit
            rows = rows[:limit]
            return {
                "kind": "query",
                "columns": columns,
                "rows": [
                    {column: _json_value(db.norm_cell(value)) for column, value in zip(columns, row)}
                    for row in rows
                ],
                "returned_rows": len(rows),
                "limit": limit,
                "truncated": truncated,
                "has_more": truncated,
            }
        except MCPServerError:
            raise
        except Exception as exc:
            self._forget_db(source_id)
            raise MCPServerError("查询失败: %s" % exc) from exc

    # ------------------------------------------------------------ compare
    def _compare_context(self, left_source_id, right_source_id, table, where=""):
        left_profile, left_db = self._with_db(left_source_id)
        right_profile, right_db = self._with_db(right_source_id)
        if left_db.dialect != right_db.dialect:
            raise MCPServerError("两个数据源的数据库类型不同，无法比对")
        name = dbcore.check_ident((table or "").strip())
        if left_db.dialect == "oracle":
            name = name.upper()
        left_meta = left_db.table_meta(name)
        right_meta = right_db.table_meta(name)
        if left_meta is None and right_meta is None:
            raise MCPServerError("两侧数据库都不存在表 %s" % name)
        if left_meta is None:
            raise MCPServerError("左侧不存在表 %s" % name)
        if right_meta is None:
            raise MCPServerError("右侧不存在表 %s" % name)
        where = _clean_where(where)
        return left_db, right_db, left_meta, right_meta, where, left_db.dialect

    def compare_structure(self, left_source_id, right_source_id, tables):
        if not isinstance(tables, list) or not tables:
            raise MCPServerError("tables 必须是非空数组")
        if len(tables) > 50:
            raise MCPServerError("单次最多比对 50 张表")
        _lp, left_db = self._with_db(left_source_id)
        _rp, right_db = self._with_db(right_source_id)
        if left_db.dialect != right_db.dialect:
            raise MCPServerError("两个数据源的数据库类型不同，无法比对")
        dialect = left_db.dialect
        results = []
        for table in tables:
            name = dbcore.check_ident((table or "").strip())
            if dialect == "oracle":
                name = name.upper()
            left_meta = left_db.table_meta(name)
            right_meta = right_db.table_meta(name)
            status, details = dbcore.structure_report(left_meta, right_meta)
            results.append({
                "table": name,
                "status": status,
                "details": details,
                "left_sql": dbcore.diff_structure(right_meta, left_meta, dialect),
                "right_sql": dbcore.diff_structure(left_meta, right_meta, dialect),
            })
        return {"dialect": dialect, "tables": results}

    def preview_data_compare(self, left_source_id, right_source_id, table, where=""):
        left_db, right_db, left_meta, right_meta, where, _dialect = self._compare_context(
            left_source_id, right_source_id, table, where)
        try:
            left_count = left_db.count_rows(left_meta, where=where)
            right_count = right_db.count_rows(right_meta, where=where)
        except Exception as exc:
            raise MCPServerError("统计数据行数失败: %s" % exc) from exc
        return {
            "table": left_meta.name,
            "where": where,
            "left_count": left_count,
            "right_count": right_count,
            "warning_threshold": dbcore.ROW_WARNING_THRESHOLD,
            "requires_confirmation": max(left_count, right_count) > dbcore.ROW_WARNING_THRESHOLD,
        }

    def compare_data(self, left_source_id, right_source_id, table, where=""):
        left_db, right_db, left_meta, right_meta, where, dialect = self._compare_context(
            left_source_id, right_source_id, table, where)
        try:
            left_rows = left_db.fetch_rows(left_meta, where=where)
            right_rows = right_db.fetch_rows(right_meta, where=where)
        except Exception as exc:
            raise MCPServerError("读取数据失败: %s" % exc) from exc
        diff = dbcore.diff_data(left_meta, left_rows, right_meta, right_rows, dialect)
        diff["ok"] = True
        diff["dialect"] = dialect
        diff["table"] = left_meta.name
        diff["where"] = where
        diff["left_count"] = len(left_rows)
        diff["right_count"] = len(right_rows)
        return diff

    # ------------------------------------------------------------ copy to test
    @staticmethod
    def _column_compatible(source_meta, target_meta):
        source_cols = {col.name.upper(): col for col in source_meta.cols}
        target_cols = {col.name.upper(): col for col in target_meta.cols}
        details = []
        for key in sorted(set(source_cols) | set(target_cols)):
            source_col = source_cols.get(key)
            target_col = target_cols.get(key)
            if source_col is None:
                details.append("目标表多出列: %s" % target_col.name)
                continue
            if target_col is None:
                details.append("目标表缺少列: %s" % source_col.name)
                continue
            if dbcore.norm_type(source_col.type) != dbcore.norm_type(target_col.type):
                details.append("列 %s 类型不同: 源=%s 目标=%s" % (
                    source_col.name, source_col.type, target_col.type))
            if bool(source_col.nullable) != bool(target_col.nullable):
                details.append("列 %s 可空性不同" % source_col.name)
        source_pk = [name.upper() for name in source_meta.pk_cols]
        target_pk = [name.upper() for name in target_meta.pk_cols]
        if source_pk != target_pk:
            details.append("主键不同: 源=(%s) 目标=(%s)" % (
                ", ".join(source_meta.pk_cols) or "无",
                ", ".join(target_meta.pk_cols) or "无"))
        return details

    @staticmethod
    def _begin_write(db):
        if db.dialect == "mysql":
            db.conn.autocommit(False)
        elif db.dialect == "sqlite":
            db.conn.execute("BEGIN")

    @staticmethod
    def _commit_write(db):
        db.conn.commit()

    @staticmethod
    def _rollback_write(db):
        try:
            db.conn.rollback()
        except Exception:
            pass

    @staticmethod
    def _placeholders(dialect, count):
        if dialect == "mysql":
            return ", ".join(["%s"] * count)
        if dialect == "oracle":
            return ", ".join([":%d" % index for index in range(1, count + 1)])
        return ", ".join(["?"] * count)

    @staticmethod
    def _is_true(value):
        return value is True or value == 1 or str(value).lower() == "true"

    def _write_profile(self, source_id):
        profile = self._profile(source_id)
        if profile.get("tag") != "test":
            raise MCPServerError("只允许操作 tag=test 的测试库，生产库始终只读")
        if not self._is_true(profile.get("allow_ai_write")):
            raise MCPServerError("该测试库未开启 allow_ai_write，不允许 AI 写操作")
        return profile

    def _audit_write(self, action, profile, sql, params, affected_rows, status, error=None):
        """Append one JSON line to the local AI write audit log."""
        record = {
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "action": action,
            "source_id": profile.get("id"),
            "source_name": profile.get("name"),
            "database": profile.get("database") or profile.get("path"),
            "sql": sql,
            "params": _json_value(params or []),
            "affected_rows": affected_rows,
            "status": status,
            "error": error,
        }
        try:
            self.audit_file.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_file.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # An audit-log failure must not undo a completed transaction, but
            # the operation result still carries the audit status.
            pass
        return record

    def _execute_test_sql(self, source_id, sql, params, preview):
        sql = _clean_dml(sql)
        if params is None:
            params = []
        if not isinstance(params, (list, tuple)):
            raise MCPServerError("params 必须是数组")
        profile = self._write_profile(source_id)
        _profile, db = self._with_db(source_id)
        affected_rows = 0
        error = None
        try:
            self._begin_write(db)
            with db.lock:
                cur = db.conn.cursor()
                try:
                    if params:
                        cur.execute(sql, list(params))
                    else:
                        cur.execute(sql)
                    affected_rows = int(cur.rowcount or 0)
                finally:
                    cur.close()
            if preview:
                self._rollback_write(db)
            else:
                self._commit_write(db)
            self._audit_write(
                "preview_sql" if preview else "execute_test_sql",
                profile, sql, params, affected_rows, "previewed" if preview else "committed",
            )
            return {
                "source_id": source_id,
                "sql": sql,
                "params": list(params),
                "affected_rows": affected_rows,
                "preview": preview,
                "executed": not preview,
            }
        except Exception as exc:
            error = str(exc)
            self._rollback_write(db)
            self._audit_write(
                "preview_sql" if preview else "execute_test_sql",
                profile, sql, params, affected_rows, "failed", error,
            )
            raise MCPServerError("SQL 执行失败，已回滚: %s" % exc) from exc
        finally:
            if db.dialect == "mysql":
                try:
                    db.conn.autocommit(True)
                except Exception:
                    pass

    def preview_sql(self, source_id, sql, params=None):
        """Execute DML in a transaction and roll back, returning affected rows."""
        return self._execute_test_sql(source_id, sql, params, preview=True)

    def execute_test_sql(self, source_id, sql, params=None):
        """Execute DML against a test source with allow_ai_write enabled."""
        return self._execute_test_sql(source_id, sql, params, preview=False)

    @staticmethod
    def _database_identity(profile):
        """Return a comparable identity for two saved profiles."""
        ptype = (profile.get("type") or "").lower()
        if ptype == "sqlite":
            return ("sqlite", str(Path(profile.get("path") or "").expanduser().resolve()))
        if ptype == "oracle":
            return (
                "oracle",
                str(profile.get("host") or "").strip().lower(),
                int(profile.get("port") or 1521),
                (profile.get("ora_mode") or "service").lower(),
                str(profile.get("service_name") or profile.get("sid") or "").strip().lower(),
            )
        return (
            ptype,
            str(profile.get("host") or "").strip().lower(),
            int(profile.get("port") or 3306),
            str(profile.get("database") or "").strip().lower(),
        )

    def copy_table_to_test(self, source_id, target_id, table, mode="replace",
                           where="", target_where="", batch_size=500, dry_run=True):
        """Copy source table rows into a target source tagged ``test``.

        This is intentionally the only MCP tool that can write.  It requires:
        - target profile tag == "test";
        - source and target table column structure (names/types/nullability/PK)
          to be compatible;
        - source and target are different data sources.

        ``where`` filters source rows.  ``target_where`` optionally limits the
        rows removed in replace mode; when omitted all target rows are removed.
        """
        source_profile = self._profile(source_id)
        target_profile = self._profile(target_id)
        if source_id == target_id:
            raise MCPServerError("源数据源和目标数据源不能相同")
        if self._database_identity(source_profile) == self._database_identity(target_profile):
            raise MCPServerError("源数据源和目标数据源指向同一个数据库，拒绝复制")
        if target_profile.get("tag") != "test":
            raise MCPServerError("目标数据源必须标记为 test，拒绝写入非测试库")
        if not self._is_true(target_profile.get("allow_ai_write")):
            raise MCPServerError("目标测试库未开启 allow_ai_write，拒绝写入")
        mode = (mode or "replace").strip().lower()
        if mode not in ("replace", "append"):
            raise MCPServerError("mode 只支持 replace 或 append")
        try:
            batch_size = int(batch_size)
        except (TypeError, ValueError) as exc:
            raise MCPServerError("batch_size 必须是 100 到 5000 的整数") from exc
        if not 100 <= batch_size <= 5000:
            raise MCPServerError("batch_size 必须是 100 到 5000 的整数")
        if not isinstance(dry_run, bool):
            raise MCPServerError("dry_run 必须是布尔值")
        where = _clean_where(where)
        target_where = _clean_where(target_where)
        table_name = dbcore.check_ident((table or "").strip())

        source_profile, source_db = self._with_db(source_id)
        target_profile, target_db = self._with_db(target_id)
        if source_db.dialect != target_db.dialect:
            raise MCPServerError("源库和目标库数据库类型不同，无法复制")
        name = table_name.upper() if source_db.dialect == "oracle" else table_name
        source_meta = source_db.table_meta(name)
        target_meta = target_db.table_meta(name)
        if source_meta is None:
            raise MCPServerError("源库不存在表: %s" % name)
        if target_meta is None:
            raise MCPServerError("目标库不存在表: %s" % name)
        details = self._column_compatible(source_meta, target_meta)
        if details:
            raise MCPServerError("源表和目标表结构不一致，不能复制: %s" % "；".join(details))
        source_count = source_db.count_rows(source_meta, where=where)
        summary = {
            "source_id": source_id,
            "target_id": target_id,
            "table": name,
            "mode": mode,
            "where": where,
            "target_where": target_where,
            "structure_compatible": True,
            "source_rows": source_count,
            "dry_run": dry_run,
        }
        if dry_run:
            summary["would_write"] = True
            return summary

        # Build target column names in source column order.
        target_by_upper = {col.name.upper(): col.name for col in target_meta.cols}
        source_names = [col.name for col in source_meta.cols]
        insert_columns = [target_by_upper[name.upper()] for name in source_names]
        source_indexes = {col.name.upper(): index for index, col in enumerate(source_meta.cols)}
        value_indexes = [source_indexes[name.upper()] for name in source_names]
        try:
            rows = source_db.fetch_rows(source_meta, where=where)
        except Exception as exc:
            raise MCPServerError("读取源表数据失败: %s" % exc) from exc
        target_q = target_db.q(name)
        column_sql = ", ".join(target_db.q(column) for column in insert_columns)
        value_sql = self._placeholders(target_db.dialect, len(insert_columns))
        insert_sql = "INSERT INTO %s (%s) VALUES (%s)" % (target_q, column_sql, value_sql)
        deleted_rows = 0
        inserted_rows = 0
        try:
            self._begin_write(target_db)
            with target_db.lock:
                cur = target_db.conn.cursor()
                try:
                    if mode == "replace":
                        delete_sql = "DELETE FROM %s" % target_q
                        if target_where:
                            delete_sql += dbcore.BaseDB._where_sql(target_where)
                        cur.execute(delete_sql)
                        deleted_rows = int(cur.rowcount or 0)
                    batch = []
                    for row in rows:
                        batch.append(tuple(row[index] for index in value_indexes))
                        if len(batch) >= batch_size:
                            cur.executemany(insert_sql, batch)
                            inserted_rows += len(batch)
                            batch = []
                    if batch:
                        cur.executemany(insert_sql, batch)
                        inserted_rows += len(batch)
                finally:
                    cur.close()
            self._commit_write(target_db)
            summary.update({
                "deleted_rows": deleted_rows,
                "inserted_rows": inserted_rows,
                "target_rows_after": target_db.count_rows(target_meta),
            })
            return summary
        except Exception as exc:
            self._rollback_write(target_db)
            raise MCPServerError("复制数据失败，已回滚: %s" % exc) from exc
        finally:
            if target_db.dialect == "mysql":
                try:
                    target_db.conn.autocommit(True)
                except Exception:
                    pass



TOOL_DEFINITIONS = [
    {
        "name": "list_data_sources",
        "description": "列出桌面工具中已经保存的数据源（不会返回密码）。tag 可区分 test/prod/dev。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "可选，按 test/prod/dev 过滤"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_tables",
        "description": "列出指定已保存数据源中的表，可按名称过滤并分页。",
        "inputSchema": {
            "type": "object",
            "required": ["source_id"],
            "properties": {
                "source_id": {"type": "string"},
                "filter": {"type": "string", "description": "可选，按表名包含匹配"},
                "limit": {"type": "integer", "minimum": 0, "default": 0},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
        },
    },
    {
        "name": "list_views",
        "description": "列出指定已保存数据源中的视图。",
        "inputSchema": {
            "type": "object",
            "required": ["source_id"],
            "properties": {"source_id": {"type": "string"}},
        },
    },
    {
        "name": "get_table_schema",
        "description": "读取表结构、主键、索引和列信息。",
        "inputSchema": {
            "type": "object",
            "required": ["source_id", "table"],
            "properties": {"source_id": {"type": "string"}, "table": {"type": "string"}},
        },
    },
    {
        "name": "get_table_schemas",
        "description": "批量读取多个表的结构，避免一次只查一张表。",
        "inputSchema": {
            "type": "object",
            "required": ["source_id", "tables"],
            "properties": {
                "source_id": {"type": "string"},
                "tables": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
            },
        },
    },
    {
        "name": "get_view_schema",
        "description": "读取视图列结构和视图定义 SQL。",
        "inputSchema": {
            "type": "object",
            "required": ["source_id", "view"],
            "properties": {"source_id": {"type": "string"}, "view": {"type": "string"}},
        },
    },
    {
        "name": "read_table",
        "description": "读取表数据；支持列选择、where、排序、分页，单次最多 500 行。",
        "inputSchema": {
            "type": "object",
            "required": ["source_id", "table"],
            "properties": {
                "source_id": {"type": "string"},
                "table": {"type": "string"},
                "where": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "order_by": {"type": "string", "description": "例如 id 或 id desc"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 500},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "count_only": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "read_view",
        "description": "读取视图数据；支持列选择、where、排序、分页，单次最多 500 行。",
        "inputSchema": {
            "type": "object",
            "required": ["source_id", "view"],
            "properties": {
                "source_id": {"type": "string"},
                "view": {"type": "string"},
                "where": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "order_by": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 500},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "count_only": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "read_tables",
        "description": "批量读取多张表的数据，每张表可以有独立的 where/columns/limit/offset。",
        "inputSchema": {
            "type": "object",
            "required": ["source_id", "requests"],
            "properties": {
                "source_id": {"type": "string"},
                "requests": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "required": ["table"],
                        "properties": {
                            "table": {"type": "string"},
                            "where": {"type": "string"},
                            "columns": {"type": "array", "items": {"type": "string"}},
                            "order_by": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 500},
                            "offset": {"type": "integer", "minimum": 0, "default": 0},
                            "count_only": {"type": "boolean", "default": False},
                        },
                    },
                },
            },
        },
    },
    {
        "name": "query",
        "description": "执行只读 SELECT/WITH 查询；适合跨表诊断。禁止写入、DDL、注释和多语句。",
        "inputSchema": {
            "type": "object",
            "required": ["source_id", "sql"],
            "properties": {
                "source_id": {"type": "string"},
                "sql": {"type": "string"},
                "params": {"type": "array", "items": {}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 500},
            },
        },
    },
    {
        "name": "compare_structure",
        "description": "对比两个数据源的表结构，并返回两侧修复 SQL（只生成不执行）。",
        "inputSchema": {
            "type": "object",
            "required": ["left_source_id", "right_source_id", "tables"],
            "properties": {
                "left_source_id": {"type": "string"},
                "right_source_id": {"type": "string"},
                "tables": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 50},
            },
        },
    },
    {
        "name": "preview_data_compare",
        "description": "预览两个数据源指定表的行数，不返回明细。",
        "inputSchema": {
            "type": "object",
            "required": ["left_source_id", "right_source_id", "table"],
            "properties": {
                "left_source_id": {"type": "string"},
                "right_source_id": {"type": "string"},
                "table": {"type": "string"},
                "where": {"type": "string"},
            },
        },
    },
    {
        "name": "compare_data",
        "description": "对比两个数据源的表数据，并返回差异摘要和两侧修复 SQL（只生成不执行）。",
        "inputSchema": {
            "type": "object",
            "required": ["left_source_id", "right_source_id", "table"],
            "properties": {
                "left_source_id": {"type": "string"},
                "right_source_id": {"type": "string"},
                "table": {"type": "string"},
                "where": {"type": "string"},
            },
        },
    },
    {
        "name": "copy_table_to_test",
        "description": "把一个表的数据复制到 tag=test 的目标库；where 过滤源表行，target_where 限制 replace 删除的目标行；默认 dry_run=true，正式库不会作为目标。",
        "inputSchema": {
            "type": "object",
            "required": ["source_id", "target_id", "table"],
            "properties": {
                "source_id": {"type": "string", "description": "源数据源 id，例如正式库"},
                "target_id": {"type": "string", "description": "目标数据源 id，必须 tag=test 且 allow_ai_write=true"},
                "table": {"type": "string"},
                "mode": {"type": "string", "enum": ["replace", "append"], "default": "replace"},
                "where": {"type": "string", "description": "可选，仅复制源表符合条件的行"},
                "target_where": {"type": "string", "description": "可选，replace 模式仅删除目标表符合条件的行；不传则清空目标表"},
                "batch_size": {"type": "integer", "minimum": 100, "maximum": 5000, "default": 500},
                "dry_run": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "preview_sql",
        "description": "在 tag=test 且 allow_ai_write=true 的测试库中预览 INSERT/UPDATE/DELETE 的影响行数；事务执行后回滚，不落盘。",
        "inputSchema": {
            "type": "object",
            "required": ["source_id", "sql"],
            "properties": {
                "source_id": {"type": "string"},
                "sql": {"type": "string", "description": "单条 INSERT/UPDATE/DELETE"},
                "params": {"type": "array", "items": {}},
            },
        },
    },
    {
        "name": "execute_test_sql",
        "description": "在 tag=test 且 allow_ai_write=true 的测试库中执行单条 INSERT/UPDATE/DELETE；禁止 DDL/DROP，失败自动回滚。",
        "inputSchema": {
            "type": "object",
            "required": ["source_id", "sql"],
            "properties": {
                "source_id": {"type": "string"},
                "sql": {"type": "string", "description": "单条 INSERT/UPDATE/DELETE"},
                "params": {"type": "array", "items": {}},
            },
        },
    },
]

SERVER_INSTRUCTIONS = (
    "这是一个数据库 MCP 服务。使用流程：先调用 list_data_sources，按返回的 name 找到目标数据源的 id；"
    "后续所有工具都把这个 id 作为 source_id 传入。data source 的 tag 字段表示 test/prod/dev，"
    "allow_ai_write 表示该测试库是否允许 AI 写操作。查询单表结构用 get_table_schema，批量结构用 get_table_schemas；"
    "读取数据用 read_table/read_view，支持 columns、order_by、offset、count_only，单次最多 500 行。"
    "跨表诊断用 read_tables 或只读 query。对象结构有 TTL 缓存；需要对比两个数据源时用 compare_structure、"
    "preview_data_compare、compare_data。copy_table_to_test 用于把数据复制到 tag=test 且 allow_ai_write=true 的"
    "目标库，要求两侧列结构一致，默认先 dry_run=true 预览。preview_sql / execute_test_sql 只允许在开启"
    "allow_ai_write 的测试库执行单条 DML；生产库始终只读。不要猜测 source_id，也不要要求用户提供数据库密码。"
)

for _tool in TOOL_DEFINITIONS:
    if _tool["name"] in ("copy_table_to_test", "execute_test_sql"):
        _tool["annotations"] = {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        }
    elif _tool["name"] == "preview_sql":
        _tool["annotations"] = {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        }
    else:
        _tool["annotations"] = {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }


def call_tool(service, name, arguments):
    args = arguments if isinstance(arguments, dict) else {}
    if name == "list_data_sources":
        profiles = service.public_profiles()
        tag = (args.get("tag") or "").strip()
        if tag:
            profiles = [profile for profile in profiles if profile.get("tag") == tag]
        return {"data_sources": profiles}
    if name == "list_tables":
        return {
            "source_id": args.get("source_id"),
            "tables": service.list_objects(
                args.get("source_id"), "table",
                name_filter=args.get("filter"),
                limit=args.get("limit", 0),
                offset=args.get("offset", 0),
            ),
        }
    if name == "list_views":
        return {"source_id": args.get("source_id"), "views": service.list_objects(args.get("source_id"), "view")}
    if name == "get_table_schema":
        return service.schema(args.get("source_id"), args.get("table"), "table")
    if name == "get_table_schemas":
        return service.schema_many(args.get("source_id"), args.get("tables"), "table")
    if name == "get_view_schema":
        return service.schema(args.get("source_id"), args.get("view"), "view")
    if name == "read_table":
        return service.data(
            args.get("source_id"), args.get("table"), "table",
            where=args.get("where", ""),
            limit=args.get("limit", MAX_RETURN_ROWS),
            columns=args.get("columns"),
            offset=args.get("offset", 0),
            order_by=args.get("order_by", ""),
            count_only=args.get("count_only", False),
        )
    if name == "read_view":
        return service.data(
            args.get("source_id"), args.get("view"), "view",
            where=args.get("where", ""),
            limit=args.get("limit", MAX_RETURN_ROWS),
            columns=args.get("columns"),
            offset=args.get("offset", 0),
            order_by=args.get("order_by", ""),
            count_only=args.get("count_only", False),
        )
    if name == "read_tables":
        return service.read_tables(args.get("source_id"), args.get("requests"))
    if name == "query":
        return service.query(
            args.get("source_id"), args.get("sql"),
            params=args.get("params"),
            limit=args.get("limit", MAX_RETURN_ROWS),
        )
    if name == "compare_structure":
        return service.compare_structure(
            args.get("left_source_id"), args.get("right_source_id"), args.get("tables"))
    if name == "preview_data_compare":
        return service.preview_data_compare(
            args.get("left_source_id"), args.get("right_source_id"),
            args.get("table"), args.get("where", ""))
    if name == "compare_data":
        return service.compare_data(
            args.get("left_source_id"), args.get("right_source_id"),
            args.get("table"), args.get("where", ""))
    if name == "copy_table_to_test":
        return service.copy_table_to_test(
            args.get("source_id"), args.get("target_id"), args.get("table"),
            mode=args.get("mode", "replace"),
            where=args.get("where", ""),
            target_where=args.get("target_where", ""),
            batch_size=args.get("batch_size", 500),
            dry_run=args.get("dry_run", True),
        )
    if name == "preview_sql":
        return service.preview_sql(
            args.get("source_id"), args.get("sql"), args.get("params"))
    if name == "execute_test_sql":
        return service.execute_test_sql(
            args.get("source_id"), args.get("sql"), args.get("params"))
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
            "serverInfo": {"name": "dbsync", "version": MCP_SERVER_VERSION},
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
        tool_name = params.get("name")
        try:
            value = call_tool(service, tool_name, params.get("arguments"))
            result = _tool_result(value)
        except Exception as exc:
            error = {"error": str(exc), "error_type": type(exc).__name__}
            if tool_name:
                error["tool"] = tool_name
            result = _tool_result(error, is_error=True)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found: %s" % method}}


def _force_utf8_stdio():
    """把 stdio 强制为 UTF-8 且不做换行翻译。

    Windows 下 stdout 被管道接管时默认编码是 GBK（cp936），而 MCP 协议要求
    UTF-8。不显式设置的话，带中文的 description/instructions 会以 GBK 字节送出，
    客户端按 UTF-8 解码即失败，整个 server 的握手和 tools/list 都会被丢弃。
    同时把 newline 固定为 \n，避免输出被翻译成 \r\n 干扰按行分隔的报文。
    """
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if stream is sys.stdout:
                reconfigure(encoding="utf-8", errors="replace", newline="\n")
            else:
                reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # 流已被重定向为非文本对象时跳过，不阻断服务启动。
            pass


def run_stdio(service=None, input_stream=None, output_stream=None):
    if input_stream is None or output_stream is None:
        _force_utf8_stdio()
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
    parser = argparse.ArgumentParser(description="Database MCP server for saved database sources")
    parser.add_argument("--store-file", type=Path, help="Override connections.json path (mainly for testing)")
    args = parser.parse_args(argv)
    service = ReadOnlyDatabaseService(store_file=args.store_file)
    try:
        run_stdio(service)
    finally:
        service.close_all()


if __name__ == "__main__":
    main()
