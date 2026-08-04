# -*- coding: utf-8 -*-
"""
自测: 用两个 SQLite 库端到端验证
  1) 结构差异 SQL 能让两侧结构一致(只执行其中一侧的SQL)
  2) 数据差异 SQL 能让两侧数据一致(只执行其中一侧的SQL)
  3) Oracle/MySQL 方言的结构 SQL 文本格式正确
运行: .venv\\Scripts\\python.exe tests\\selftest.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import dbcore  # noqa: E402

TABLES = ["T1", "T2", "T3"]


def build(path, ddl_list, rows_map):
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    for ddl in ddl_list:
        conn.execute(ddl)
    for t, rows in rows_map.items():
        conn.executemany("INSERT INTO %s VALUES (%s)" % (t, ",".join("?" * len(rows[0]))), rows)
    conn.commit()
    return conn


def make_pair(tmp):
    """造一对有结构+数据差异的库"""
    lp = os.path.join(tmp, "left.db")
    rp = os.path.join(tmp, "right.db")
    lconn = build(lp, [
        "CREATE TABLE T1 (a INTEGER PRIMARY KEY, b VARCHAR(20), c NUMERIC(10,2))",
        "CREATE TABLE T3 (x INTEGER)",
    ], {"T1": [(1, "x", 1.5), (2, "y", 2.0), (3, "z", None)],
        "T3": [(1,)]})
    rconn = build(rp, [
        "CREATE TABLE T1 (a INTEGER PRIMARY KEY, b VARCHAR(30), d TEXT)",
        "CREATE TABLE T2 (id INTEGER PRIMARY KEY, name TEXT)",
    ], {"T1": [(1, "x", "keep"), (2, "yy", "chg"), (4, "w", "new")],
        "T2": [(1, "n1")]})
    return dbcore.SQLiteDB(lconn), dbcore.SQLiteDB(rconn)


def gen_struct_sql(ldb, rdb):
    """返回 (left_sql, right_sql, results)"""
    l_all, r_all, results = [], [], []
    for t in TABLES:
        lm, rm = ldb.table_meta(t), rdb.table_meta(t)
        status, details = dbcore.structure_report(lm, rm)
        results.append((t, status, details))
        l_all += dbcore.diff_structure(rm, lm, "sqlite")   # 让左侧变成右侧
        r_all += dbcore.diff_structure(lm, rm, "sqlite")   # 让右侧变成左侧
    strip = lambda ss: "\n".join(s for s in ss if not s.startswith("--"))
    return strip(l_all), strip(r_all), results


def check_all_same(ldb, rdb, tag):
    for t in TABLES:
        status, details = dbcore.structure_report(ldb.table_meta(t), rdb.table_meta(t))
        assert status in ("same", "missing_both"), "%s 结构仍不一致: %s %s" % (tag, t, details)
    lm, rm = ldb.table_meta("T1"), rdb.table_meta("T1")
    d = dbcore.diff_data(lm, ldb.fetch_rows(lm), rm, rdb.fetch_rows(rm), "sqlite")
    assert d["only_left"] == 0 and d["only_right"] == 0 and d["updated"] == 0, \
        "%s 数据仍不一致: %s" % (tag, {k: d[k] for k in ("only_left", "only_right", "updated")})
    print("[OK] %s: 结构与数据均已一致" % tag)


def main():
    tmp = tempfile.mkdtemp(prefix="dbsync_test_")

    # 启动时只清理 WebView 网页资源缓存，不能删除保存比对历史的 Local Storage。
    webview_storage = Path(tmp) / "webview"
    for relative in app.WEBVIEW_ASSET_CACHE_DIRS:
        cache_file = webview_storage / relative / "cached-file"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text("cached", encoding="utf-8")
    history_file = webview_storage / "EBWebView" / "Default" / "Local Storage" / "history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text("keep", encoding="utf-8")
    app.clear_webview_asset_cache(webview_storage)
    assert all(not (webview_storage / relative).exists()
               for relative in app.WEBVIEW_ASSET_CACHE_DIRS)
    assert history_file.read_text(encoding="utf-8") == "keep"

    # ---------- 第一轮: 只执行左侧修复SQL(左侧变成右侧) ----------
    ldb, rdb = make_pair(tmp)
    l_sql, r_sql, results = gen_struct_sql(ldb, rdb)
    for t, st, details in results:
        print("[结构] %-4s -> %-10s %s" % (t, st, "; ".join(details)))
    assert ("T1", "diff") == (results[0][0], results[0][1])
    assert results[1][1] == "only_right" and results[2][1] == "only_left"
    print("--- 左侧结构修复SQL ---\n" + l_sql)

    # 数据差异(修复前先验证识别正确)
    lm, rm = ldb.table_meta("T1"), rdb.table_meta("T1")
    # 结构未对齐时列不一致应报错
    try:
        dbcore.diff_data(lm, ldb.fetch_rows(lm), rm, rdb.fetch_rows(rm), "sqlite")
        raise SystemExit("应抛出列不一致错误")
    except dbcore.DBError as e:
        print("[数据] 结构未对齐时正确拦截: %s" % e)

    ldb.conn.executescript(l_sql)                     # 只动左侧
    print("[OK] 左侧结构SQL执行成功")
    # 结构已对齐, 现在比对数据
    lm, rm = ldb.table_meta("T1"), rdb.table_meta("T1")
    diff = dbcore.diff_data(lm, ldb.fetch_rows(lm), rm, rdb.fetch_rows(rm), "sqlite")
    print("[数据] T1 修复前: 仅左=%d 仅右=%d 更新=%d" % (diff["only_left"], diff["only_right"], diff["updated"]))
    # a=3 仅左侧, a=4 仅右侧, a=1/a=2 内容不同(重建后左侧d列为NULL)
    assert diff["only_left"] == 1 and diff["only_right"] == 1 and diff["updated"] == 2
    print("--- 左侧数据修复SQL ---\n" + diff["left_sql"])
    ldb.conn.executescript(diff["left_sql"])          # 只动左侧
    check_all_same(ldb, rdb, "只执行左侧SQL后")
    ldb.close(); rdb.close()

    # ---------- 第二轮: 只执行右侧修复SQL(右侧变成左侧) ----------
    ldb, rdb = make_pair(tmp)
    l_sql, r_sql, _ = gen_struct_sql(ldb, rdb)
    print("--- 右侧结构修复SQL ---\n" + r_sql)
    rdb.conn.executescript(r_sql)                     # 只动右侧
    print("[OK] 右侧结构SQL执行成功")
    lm, rm = ldb.table_meta("T1"), rdb.table_meta("T1")
    diff = dbcore.diff_data(lm, ldb.fetch_rows(lm), rm, rdb.fetch_rows(rm), "sqlite")
    print("--- 右侧数据修复SQL ---\n" + diff["right_sql"])
    rdb.conn.executescript(diff["right_sql"])         # 只动右侧
    check_all_same(ldb, rdb, "只执行右侧SQL后")
    ldb.close(); rdb.close()

    # ---------- Oracle / MySQL 方言 SQL 文本 ----------
    src = dbcore.TableMeta("EMP", [
        dbcore.Col("ID", "NUMBER(10)", False, None, 1),
        dbcore.Col("NAME", "VARCHAR2(100 CHAR)", True, "'anon'"),
        dbcore.Col("HIREDATE", "DATE", True),
        dbcore.Col("EXTRA", "NUMBER(5,2)", True),
    ])
    dst = dbcore.TableMeta("EMP", [
        dbcore.Col("ID", "NUMBER(10)", False, None, 1),
        dbcore.Col("NAME", "VARCHAR2(50)", True),
        dbcore.Col("OLD_COL", "CHAR(2)", True),
    ])
    o_sql = dbcore.diff_structure(src, dst, "oracle")
    m_sql = dbcore.diff_structure(src, dst, "mysql")
    print("\n--- Oracle 方言 ---")
    for s in o_sql:
        print(s)
    print("--- MySQL 方言 ---")
    for s in m_sql:
        print(s)
    joined_o = "\n".join(o_sql)
    assert "ALTER TABLE EMP ADD (" in joined_o and "EXTRA NUMBER(5,2)" in joined_o
    assert "ALTER TABLE EMP MODIFY (NAME VARCHAR2(100 CHAR) DEFAULT 'anon');" in joined_o
    assert "ALTER TABLE EMP DROP COLUMN OLD_COL;" in joined_o
    assert any("CREATE TABLE EMP" in s for s in dbcore.diff_structure(src, None, "oracle"))
    assert any("DROP TABLE EMP;" in s for s in dbcore.diff_structure(None, dst, "oracle"))
    joined_m = "\n".join(m_sql)
    assert "ALTER TABLE `EMP` ADD COLUMN `EXTRA` DECIMAL(5,2);" not in joined_m  # 用源类型原样
    assert "ADD COLUMN `EXTRA` NUMBER(5,2);" in joined_m
    assert "MODIFY COLUMN `NAME` VARCHAR2(100 CHAR) DEFAULT 'anon' NULL;" in joined_m

    # 整表缺失时必须生成完整 Oracle DDL：索引、命名约束、表备注和列备注都不能丢。
    full_oracle = dbcore.TableMeta(
        "SJ_CARRIER_MINI_VISITOR",
        [
            dbcore.Col("ID", "NUMBER(19)", False, None, 1, "主键"),
            dbcore.Col("APP_ID", "VARCHAR2(64)", False, None, 0, "微信小程序AppID"),
            dbcore.Col("OPEN_ID", "VARCHAR2(128)", False, None, 0, "微信用户OpenID"),
        ],
        indexes=[dbcore.IndexMeta(
            "UK_MINI_USER_APP_OPEN", ["APP_ID", "OPEN_ID"], unique=True)],
        table_comment="承运商小程序游客记录表",
        pk_name="PK_CARRIER_MINI_VISITOR",
        pk_index_name="PK_CARRIER_MINI_USER",
        unique_constraints=[dbcore.UniqueConstraintMeta(
            "UK_MINI_VISITOR_APP_OPEN", ["APP_ID", "OPEN_ID"],
            "UK_MINI_USER_APP_OPEN")],
    )
    full_o_sql = "\n".join(dbcore.diff_structure(full_oracle, None, "oracle"))
    assert "CREATE UNIQUE INDEX PK_CARRIER_MINI_USER ON SJ_CARRIER_MINI_VISITOR (ID);" in full_o_sql
    assert "CREATE UNIQUE INDEX UK_MINI_USER_APP_OPEN ON SJ_CARRIER_MINI_VISITOR (APP_ID, OPEN_ID);" in full_o_sql
    assert ("ALTER TABLE SJ_CARRIER_MINI_VISITOR ADD CONSTRAINT PK_CARRIER_MINI_VISITOR "
            "PRIMARY KEY (ID) USING INDEX PK_CARRIER_MINI_USER;") in full_o_sql
    assert ("ALTER TABLE SJ_CARRIER_MINI_VISITOR ADD CONSTRAINT UK_MINI_VISITOR_APP_OPEN "
            "UNIQUE (APP_ID, OPEN_ID) USING INDEX UK_MINI_USER_APP_OPEN;") in full_o_sql
    assert "COMMENT ON TABLE SJ_CARRIER_MINI_VISITOR IS '承运商小程序游客记录表';" in full_o_sql
    assert "COMMENT ON COLUMN SJ_CARRIER_MINI_VISITOR.ID IS '主键';" in full_o_sql

    # MySQL / SQLite 的整表缺失分支也应带上已读取到的索引和备注。
    full_mysql = dbcore.TableMeta(
        "T_FULL",
        [dbcore.Col("ID", "BIGINT", False, None, 1, "主键")],
        indexes=[dbcore.IndexMeta("IDX_FULL_ID", ["ID"])],
        table_comment="完整表",
    )
    full_m_sql = "\n".join(dbcore.diff_structure(full_mysql, None, "mysql"))
    assert "CREATE INDEX `IDX_FULL_ID` ON `T_FULL` (`ID`);" in full_m_sql
    assert "ALTER TABLE `T_FULL` COMMENT = '完整表';" in full_m_sql
    assert "ALTER TABLE `T_FULL` MODIFY COLUMN `ID` BIGINT NOT NULL COMMENT '主键';" in full_m_sql
    full_s_sql = "\n".join(dbcore.diff_structure(full_mysql, None, "sqlite"))
    assert 'CREATE INDEX "IDX_FULL_ID" ON "T_FULL" ("ID");' in full_s_sql

    # Oracle 仅在可空性确实变化时输出 NULL / NOT NULL，避免默认值差异触发 ORA-01451
    default_src = dbcore.TableMeta("T", [dbcore.Col("CREATE_TIME", "DATE", True, "sysdate")])
    default_dst = dbcore.TableMeta("T", [dbcore.Col("CREATE_TIME", "DATE", True, None)])
    assert dbcore.diff_structure(default_src, default_dst, "oracle") == [
        "ALTER TABLE T MODIFY (CREATE_TIME DATE DEFAULT sysdate);"
    ]
    commented_default_src = dbcore.TableMeta(
        "SJ_CARRIER_RECRUIT",
        [dbcore.Col("UPDATE_TIME", "DATE", True, "sysdate       -- 更新时间")],
    )
    commented_default_dst = dbcore.TableMeta(
        "SJ_CARRIER_RECRUIT",
        [dbcore.Col("UPDATE_TIME", "DATE", True, None)],
    )
    assert dbcore.diff_structure(commented_default_src, commented_default_dst, "oracle") == [
        "ALTER TABLE SJ_CARRIER_RECRUIT MODIFY (UPDATE_TIME DATE DEFAULT sysdate);"
    ]
    assert dbcore.norm_default("'A--B'") == "'A--B'"
    assert dbcore.norm_default("q'[A--B]'") == "q'[A--B]'"
    assert dbcore.norm_default("sysdate /* 更新时间 */") == "sysdate"
    assert dbcore.structure_detail_side("仅左侧有列 COL_LEFT (NUMBER)") == "left"
    assert dbcore.structure_detail_side("仅右侧有索引 IDX_RIGHT") == "right"
    assert dbcore.structure_detail_side("列 UPDATE_TIME 默认值不同") == ""
    nullable_src = dbcore.TableMeta("T", [dbcore.Col("C", "VARCHAR2(10)", True)])
    not_null_dst = dbcore.TableMeta("T", [dbcore.Col("C", "VARCHAR2(10)", False)])
    assert dbcore.diff_structure(nullable_src, not_null_dst, "oracle") == [
        "ALTER TABLE T MODIFY (C VARCHAR2(10) NULL);"
    ]
    assert dbcore.diff_structure(not_null_dst, nullable_src, "oracle") == [
        "ALTER TABLE T MODIFY (C VARCHAR2(10) NOT NULL);"
    ]

    # 字面量
    import datetime, decimal
    assert dbcore.literal(decimal.Decimal("1.50"), "oracle") == "1.50"
    assert dbcore.literal("a'b", "oracle") == "'a''b'"
    assert dbcore.literal(datetime.datetime(2026, 7, 30, 12, 0, 0), "oracle").startswith("TO_DATE(")
    assert dbcore.literal(datetime.datetime(2026, 7, 30, 12, 0, 0, 123456), "oracle").startswith("TO_TIMESTAMP(")
    assert dbcore.literal(None, "mysql") == "NULL"
    assert dbcore.literal(datetime.date(2026, 7, 30), "mysql") == "'2026-07-30'"

    print("\n===== 全部自测通过 =====")


if __name__ == "__main__":
    main()
