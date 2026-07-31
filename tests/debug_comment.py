# -*- coding: utf-8 -*-
"""验证 Oracle 列备注读取"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import dbcore

# thick 初始化
dbcore._init_oracle_thick()

import oracledb
dsn = oracledb.makedsn("192.168.111.86", 1521, service_name="sjtstms")
conn = oracledb.connect(user="sjcms", password="sjcms#0727", dsn=dsn)
db = dbcore.OracleDB(conn)

t = "SJ_CARRIER_VEHICLE_STOP_DRIVE"
m = db.table_meta(t)
if m is None:
    print("表不存在:", t)
else:
    print("表:", t, "表备注:", repr(m.table_comment))
    for c in m.cols:
        print("  列:", c.name, "  备注:", repr(c.comment))
conn.close()
