# -*- coding: utf-8 -*-
"""验证数据比对的预警和明细展示边界。"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
import dbcore


def make_db(row_count):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE T1 (id INTEGER PRIMARY KEY)")
    conn.executemany("INSERT INTO T1 VALUES (?)", ((i,) for i in range(1, row_count + 1)))
    conn.commit()
    return dbcore.SQLiteDB(conn)


left = make_db(501)
right = make_db(500)
api = app.Api()
api._sides = {
    "left": {"profile": {"type": "sqlite"}, "db": left},
    "right": {"profile": {"type": "sqlite"}, "db": right},
}

preview = api.preview_data_compare("T1")
assert preview["ok"]
assert preview["left_count"] == 501 and preview["right_count"] == 500
assert preview["requires_confirmation"] is True

filtered = api.preview_data_compare("T1", "id <= 500")
assert filtered["ok"]
assert filtered["left_count"] == 500 and filtered["right_count"] == 500
assert filtered["requires_confirmation"] is False

meta = dbcore.TableMeta("T1", [dbcore.Col("id", "INTEGER", False, None, 1)])
at_limit = dbcore.diff_data(meta, [], meta, [(i,) for i in range(2000)], "sqlite")
assert len(at_limit["details"]) == 2000
assert at_limit["detail_capped"] is False

over_limit = dbcore.diff_data(meta, [], meta, [(i,) for i in range(2001)], "sqlite")
assert len(over_limit["details"]) == 200
assert over_limit["detail_capped"] is True

sql_over_limit = dbcore.diff_data(meta, [], meta, [(i,) for i in range(5001)], "sqlite")
assert sql_over_limit["sql_capped"] is True
assert "仅输出前 5000 条" in sql_over_limit["left_sql"]

left.close()
right.close()
print("===== 数据量保护边界测试通过 =====")
