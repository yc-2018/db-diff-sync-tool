# -*- coding: utf-8 -*-
"""验证 fetch_rows 的 where 参数"""
import os, sqlite3, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
import dbcore

tmp = tempfile.mkdtemp()
p = os.path.join(tmp, "w.db")
conn = sqlite3.connect(p)
conn.executescript("""
CREATE TABLE T1 (a INTEGER PRIMARY KEY, b TEXT);
INSERT INTO T1 VALUES (1, 'x');
INSERT INTO T1 VALUES (2, 'y');
INSERT INTO T1 VALUES (3, 'z');
""")
conn.commit()
db = dbcore.SQLiteDB(conn)
m = db.table_meta("T1")

# 无 where
rows = db.fetch_rows(m)
assert len(rows) == 3, "no where: %d" % len(rows)

# 带 where
rows2 = db.fetch_rows(m, where="a > 1")
assert len(rows2) == 2, "where a>1: %d" % len(rows2)

# 带前缀的 where (此时已由 app 层剩下条件, fetch_rows 不再重复剩前缀)
# 只剩条件部分
rows3 = db.fetch_rows(m, where="a = 1")
assert len(rows3) == 1, "where a=1: %d" % len(rows3)

# count_rows 与正式读取使用相同的 where 规则
assert db.count_rows(m) == 3
assert db.count_rows(m, where="a > 1") == 2

# 注入拦截
try:
    db.fetch_rows(m, where="1=1; DROP TABLE T1")
    raise SystemExit("should reject semicolon")
except dbcore.DBError as e:
    print("[OK] blocked: %s" % e)

try:
    db.count_rows(m, where="1=1; DROP TABLE T1")
    raise SystemExit("count_rows should reject semicolon")
except dbcore.DBError as e:
    print("[OK] count blocked: %s" % e)


def make_side(has_filter_column):
    side_conn = sqlite3.connect(":memory:")
    column_sql = ", status INTEGER" if has_filter_column else ""
    side_conn.execute("CREATE TABLE T1 (a INTEGER PRIMARY KEY%s)" % column_sql)
    side_conn.execute("INSERT INTO T1 (a) VALUES (1)")
    side_conn.commit()
    return dbcore.SQLiteDB(side_conn)


# WHERE 字段只存在一侧时，错误必须明确标出失败侧。
for missing_side in ("left", "right"):
    left = make_side(missing_side != "left")
    right = make_side(missing_side != "right")
    api = app.Api()
    api._sides = {
        "left": {"profile": {"type": "sqlite"}, "db": left},
        "right": {"profile": {"type": "sqlite"}, "db": right},
    }
    result = api.preview_data_compare("T1", "status = 1")
    expected = "左侧数据库 WHERE 条件执行失败" if missing_side == "left" else "右侧数据库 WHERE 条件执行失败"
    assert not result["ok"] and expected in result["msg"], result
    left.close()
    right.close()

print("===== where 参数测试通过 =====")
