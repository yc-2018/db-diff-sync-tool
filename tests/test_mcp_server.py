# -*- coding: utf-8 -*-
"""MCP read-only service tests using temporary SQLite data sources."""
import base64
import io
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
MCP_DIR = ROOT_DIR / "mcp"
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(MCP_DIR))

import dbcore
import server as mcp_server


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    db_path = root / "agent.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT)")
    conn.executemany("INSERT INTO items VALUES (?, ?)", ((i, "item-%d" % i) for i in range(1, 601)))
    conn.execute("CREATE TABLE stores (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO stores VALUES (?, ?)", ((i, "store-%d" % i) for i in range(1, 4)))
    conn.execute("CREATE VIEW item_view AS SELECT id, label FROM items WHERE id <= 3")
    conn.commit()
    conn.close()

    right_path = root / "agent_right.db"
    conn = sqlite3.connect(str(right_path))
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT)")
    conn.executemany("INSERT INTO items VALUES (?, ?)", ((i, "item-%d" % i) for i in range(1, 600)))
    conn.execute("CREATE TABLE stores (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO stores VALUES (?, ?)", ((i, "store-%d" % i) for i in range(1, 4)))
    conn.execute("CREATE VIEW item_view AS SELECT id, label FROM items WHERE id <= 3")
    conn.commit()
    conn.close()

    store = root / "connections.json"
    store.write_text(json.dumps([
        {
            "id": "source-1", "name": "测试 SQLite", "type": "sqlite", "path": str(db_path),
            "user": "ignored", "password_enc": base64.b64encode(b"secret").decode("ascii"),
            "tag": "test",
        },
        {
            "id": "source-2", "name": "测试 SQLite 右侧", "type": "sqlite", "path": str(right_path),
            "user": "ignored", "password_enc": base64.b64encode(b"secret2").decode("ascii"),
            "tag": "test", "allow_ai_write": True,
        },
        {
            "id": "source-prod", "name": "正式 SQLite 源", "type": "sqlite", "path": str(db_path),
            "user": "ignored", "password_enc": base64.b64encode(b"secret3").decode("ascii"),
            "tag": "prod",
        },
    ], ensure_ascii=False), encoding="utf-8")
    service = mcp_server.ReadOnlyDatabaseService(store_file=store)

    listed = service.public_profiles()
    assert listed[0]["id"] == "source-1" and listed[0]["is_test"] is True
    assert "password" not in listed[0] and "password_enc" not in listed[0]
    assert service.list_objects("source-1", "table") == ["items", "stores"]
    assert service.list_objects("source-1", "view") == ["item_view"]

    table_schema = service.schema("source-1", "items", "table")
    assert table_schema["columns"][0]["name"] == "id"
    view_schema = service.schema("source-1", "item_view", "view")
    assert view_schema["definition"]

    # 批量结构
    schemas = service.schema_many("source-1", ["items", "stores"], "table")
    assert [item["name"] for item in schemas["schemas"]] == ["items", "stores"]
    assert schemas["errors"] == []

    rows = service.data("source-1", "items", "table")
    assert rows["returned_rows"] == 500 and len(rows["rows"]) == 500
    assert rows["truncated"] is True
    assert service.data("source-1", "item_view", "view")["returned_rows"] == 3

    # 列选择、分页、排序、计数
    page = service.data(
        "source-1", "items", "table", columns=["id"], order_by="id desc",
        limit=5, offset=10,
    )
    assert page["columns"] == ["id"]
    assert [row["id"] for row in page["rows"]] == [590, 589, 588, 587, 586]
    assert page["has_more"] is True
    count = service.data("source-1", "items", "table", count_only=True)
    assert count["row_count"] == 600

    # 批量读取
    batch = service.read_tables("source-1", [
        {"table": "items", "where": "id <= 2", "columns": ["id", "label"], "order_by": "id"},
        {"table": "stores", "count_only": True},
    ])
    assert len(batch["results"][0]["rows"]) == 2
    assert batch["results"][1]["row_count"] == 3

    # 只读 query
    queried = service.query("source-1", "SELECT id, label FROM items WHERE id <= 3 ORDER BY id", limit=2)
    assert queried["returned_rows"] == 2 and queried["truncated"] is True
    try:
        service.query("source-1", "DELETE FROM items")
        raise AssertionError("write SQL should be rejected")
    except mcp_server.MCPServerError:
        pass

    # 跨库对比
    structure = service.compare_structure("source-1", "source-2", ["items"])
    assert structure["tables"][0]["table"] == "items"
    preview = service.preview_data_compare("source-1", "source-2", "items")
    assert preview["left_count"] == 600 and preview["right_count"] == 599
    compared = service.compare_data("source-1", "source-2", "items")
    assert compared["ok"] is True and compared["only_left"] == 1

    # 测试库复制：先 dry-run，再实际替换目标表
    copy_preview = service.copy_table_to_test(
        "source-prod", "source-2", "items", dry_run=True)
    assert copy_preview["structure_compatible"] is True
    assert copy_preview["source_rows"] == 600
    filtered_preview = service.copy_table_to_test(
        "source-prod", "source-2", "items",
        where="id <= 5", target_where="id > 100", dry_run=True)
    assert filtered_preview["source_rows"] == 5
    assert filtered_preview["target_where"] == "id > 100"
    copy_result = service.copy_table_to_test(
        "source-prod", "source-2", "items", mode="replace", dry_run=False)
    assert copy_result["inserted_rows"] == 600
    assert copy_result["target_rows_after"] == 600
    assert service.data("source-2", "items", "table", count_only=True)["row_count"] == 600

    # 测试库受控写 SQL：preview 回滚，execute 提交
    preview_sql = service.preview_sql(
        "source-2", "UPDATE items SET label = ? WHERE id <= ?", ["preview", 2])
    assert preview_sql["affected_rows"] == 2 and preview_sql["preview"] is True
    unchanged = service.data("source-2", "items", "table", where="id = 1")
    assert unchanged["rows"][0]["label"] == "item-1"
    execute_sql = service.execute_test_sql(
        "source-2", "UPDATE items SET label = ? WHERE id = ?", ["changed", 1])
    assert execute_sql["affected_rows"] == 1 and execute_sql["executed"] is True
    changed = service.data("source-2", "items", "table", where="id = 1")
    assert changed["rows"][0]["label"] == "changed"
    try:
        service.execute_test_sql("source-1", "UPDATE items SET label = 'x' WHERE id = 1")
        raise AssertionError("source without allow_ai_write should be rejected")
    except mcp_server.MCPServerError:
        pass
    try:
        service.execute_test_sql("source-2", "DROP TABLE items")
        raise AssertionError("DDL should be rejected")
    except mcp_server.MCPServerError:
        pass

    # 非 test 目标库拒绝写入
    try:
        service.copy_table_to_test("source-1", "source-prod", "items", dry_run=False)
        raise AssertionError("non-test target should be rejected")
    except mcp_server.MCPServerError:
        pass

    try:
        service.data("source-1", "items", "table", limit=501)
        raise AssertionError("limit should be capped")
    except mcp_server.MCPServerError:
        pass
    try:
        service.data("source-1", "items", "table", where="id > 1; DROP TABLE items")
        raise AssertionError("injection should be rejected")
    except mcp_server.MCPServerError:
        pass

    init = mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, service)
    assert init["result"]["capabilities"]["tools"] == {}
    assert "list_data_sources" in init["result"]["instructions"]
    assert all(
        tool["annotations"]["readOnlyHint"]
        for tool in mcp_server.TOOL_DEFINITIONS
        if tool["name"] not in ("copy_table_to_test", "preview_sql", "execute_test_sql")
    )
    assert next(
        tool for tool in mcp_server.TOOL_DEFINITIONS if tool["name"] == "copy_table_to_test"
    )["annotations"]["readOnlyHint"] is False
    assert next(
        tool for tool in mcp_server.TOOL_DEFINITIONS if tool["name"] == "execute_test_sql"
    )["annotations"]["destructiveHint"] is True
    call = mcp_server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "list_data_sources", "arguments": {},
    }}, service)
    assert "secret" not in call["result"]["content"][0]["text"]

    stream_in = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}) + "\n")
    stream_out = io.StringIO()
    mcp_server.run_stdio(service, stream_in, stream_out)
    assert json.loads(stream_out.getvalue())["result"]["tools"]

    # 连接复用：同一次服务生命周期内不应重复 connect
    connect_calls = []

    def counting_connector(profile):
        connect_calls.append(profile["id"])
        return dbcore.SQLiteDB(sqlite3.connect(profile["path"], check_same_thread=False))

    cached_service = mcp_server.ReadOnlyDatabaseService(store_file=store, connector=counting_connector)
    cached_service.list_objects("source-1", "table")
    cached_service.schema("source-1", "items", "table")
    cached_service.data("source-1", "items", "table", limit=2)
    cached_service.schema_many("source-1", ["items", "stores"], "table")
    assert connect_calls == ["source-1"]
    cached_service.close_all()
    service.close_all()

print("===== MCP 只读服务测试通过 =====")
