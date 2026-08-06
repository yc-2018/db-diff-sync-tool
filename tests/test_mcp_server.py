# -*- coding: utf-8 -*-
"""MCP read-only service tests using a temporary SQLite data source."""
import base64
import io
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent.parent / "mcp"
sys.path.insert(0, str(MCP_DIR))

import server as mcp_server


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    db_path = root / "agent.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT)")
    conn.executemany("INSERT INTO items VALUES (?, ?)", ((i, "item-%d" % i) for i in range(1, 601)))
    conn.execute("CREATE VIEW item_view AS SELECT id, label FROM items WHERE id <= 3")
    conn.commit()
    conn.close()

    store = root / "connections.json"
    store.write_text(json.dumps([{
        "id": "source-1", "name": "测试 SQLite", "type": "sqlite", "path": str(db_path),
        "user": "ignored", "password_enc": base64.b64encode(b"secret").decode("ascii"),
    }], ensure_ascii=False), encoding="utf-8")
    service = mcp_server.ReadOnlyDatabaseService(store_file=store)

    listed = service.public_profiles()
    assert listed[0]["id"] == "source-1"
    assert "password" not in listed[0] and "password_enc" not in listed[0]
    assert service.list_objects("source-1", "table") == ["items"]
    assert service.list_objects("source-1", "view") == ["item_view"]

    table_schema = service.schema("source-1", "items", "table")
    assert table_schema["columns"][0]["name"] == "id"
    view_schema = service.schema("source-1", "item_view", "view")
    assert view_schema["definition"]

    rows = service.data("source-1", "items", "table")
    assert rows["returned_rows"] == 500 and len(rows["rows"]) == 500
    assert rows["truncated"] is True
    assert service.data("source-1", "item_view", "view")["returned_rows"] == 3

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
    assert all(tool["annotations"]["readOnlyHint"] for tool in mcp_server.TOOL_DEFINITIONS)
    call = mcp_server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "list_data_sources", "arguments": {},
    }}, service)
    assert "secret" not in call["result"]["content"][0]["text"]

    stream_in = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}) + "\n")
    stream_out = io.StringIO()
    mcp_server.run_stdio(service, stream_in, stream_out)
    assert json.loads(stream_out.getvalue())["result"]["tools"]

print("===== MCP 只读服务测试通过 =====")
