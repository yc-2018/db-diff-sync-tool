# -*- coding: utf-8 -*-
"""验证常见 JDBC URL / DSN 的连接配置解析。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dbcore


oracle_sid = dbcore.parse_connect_url(
    "jdbc:oracle:thin:demo_user/demo_password@db.example.test:1521:ORCL"
)
assert oracle_sid == {
    "type": "oracle", "host": "db.example.test", "port": 1521,
    "ora_mode": "sid", "sid": "ORCL", "service_name": "",
    "user": "demo_user", "password": "demo_password",
}

oracle_service = dbcore.parse_connect_url("jdbc:oracle:thin:@//db.example.test:1521/appsvc")
assert oracle_service == {
    "type": "oracle", "host": "db.example.test", "port": 1521,
    "ora_mode": "service", "sid": "", "service_name": "appsvc",
}

mysql_jdbc = dbcore.parse_connect_url("jdbc:mysql://db.example.test:3306/exchange")
assert mysql_jdbc == {
    "type": "mysql", "host": "db.example.test", "port": 3306,
    "database": "exchange", "user": "", "password": "",
}

mysql_with_params = dbcore.parse_connect_url(
    "jdbc:mysql://db.example.test:3306/Datax_web?serverTimezone=Asia/Shanghai&useSSL=false"
)
assert mysql_with_params["database"] == "Datax_web"
assert mysql_with_params["host"] == "db.example.test"

mysql_legacy = dbcore.parse_connect_url("mysql://demo_user:demo_password@db.example.test:3306/exchange")
assert mysql_legacy["user"] == "demo_user"
assert mysql_legacy["password"] == "demo_password"

mysql_encoded = dbcore.parse_connect_url("jdbc:mysql://demo%40user:pass%2Fword@db.example.test/encoded_db")
assert mysql_encoded["port"] == 3306
assert mysql_encoded["user"] == "demo@user"
assert mysql_encoded["password"] == "pass/word"

print("===== 连接串解析测试通过 =====")
