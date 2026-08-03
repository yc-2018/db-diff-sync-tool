# -*- coding: utf-8 -*-
"""验证连接配置可以脱离数据库连接独立保存。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    app.STORE_DIR = root
    app.STORE_FILE = root / "connections.json"
    app.SESSION_FILE = root / "session.json"

    api = app.Api()

    empty_test = api.test_profile({})
    assert empty_test["ok"] is False
    assert "数据库类型" in empty_test["msg"]

    empty_save = api.save_profile({})
    assert empty_save["ok"] is False
    assert "配置名" in empty_save["msg"]
    assert app.load_profiles() == []

    invalid_profiles = [
        ({"type": "oracle", "name": "缺主机"}, "主机"),
        ({"type": "oracle", "name": "缺用户名", "host": "db.example.test", "port": 1521}, "用户名"),
        ({"type": "oracle", "name": "缺服务名", "host": "db.example.test", "port": 1521,
          "user": "demo", "ora_mode": "service"}, "服务名"),
        ({"type": "mysql", "name": "缺数据库", "host": "db.example.test", "port": 3306,
          "user": "demo"}, "数据库名"),
        ({"type": "sqlite", "name": "缺文件"}, "文件路径"),
    ]
    for invalid, expected in invalid_profiles:
        result = api.save_profile(invalid)
        assert result["ok"] is False
        assert expected in result["msg"]

    profile = {
        "type": "oracle",
        "name": "离线配置",
        "host": "db.example.test",
        "port": 1521,
        "user": "demo_user",
        "password": "demo_password",
        "ora_mode": "sid",
        "sid": "ORCL",
        "service_name": "",
    }

    saved = api.save_profile(profile)
    assert saved["ok"] is True
    assert saved["profile"]["id"]
    assert saved["left"] is None and saved["right"] is None
    assert app.load_profiles()[0]["password"] == "demo_password"

    raw = json.loads(app.STORE_FILE.read_text(encoding="utf-8"))[0]
    assert "password" not in raw
    assert raw["password_enc"] != "demo_password"

    edited = dict(saved["profile"])
    edited["host"] = "new-db.example.test"
    edited["password"] = ""
    updated = api.save_profile(edited)
    assert updated["ok"] is True
    assert updated["profile"]["id"] == saved["profile"]["id"]
    assert updated["profile"]["password"] == "demo_password"
    assert app.load_profiles()[0]["host"] == "new-db.example.test"

    duplicate = dict(profile)
    duplicate["id"] = ""
    duplicate["host"] = "another.example.test"
    duplicate_result = api.save_profile(duplicate)
    assert duplicate_result["ok"] is False
    assert "已存在" in duplicate_result["msg"]

    deleted = api.delete_profile(saved["profile"]["id"])
    assert deleted["ok"] is True
    assert deleted["profiles"] == []
    assert deleted["left"] is None and deleted["right"] is None
    assert app.load_profiles() == []

print("===== 连接配置独立保存测试通过 =====")
