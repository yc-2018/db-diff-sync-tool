# -*- coding: utf-8 -*-
"""
数据库比对核心模块
支持: Oracle(优先) / MySQL / SQLite(便于本机试用与自测)
功能:
  1. 连接管理(由各方言类封装)
  2. 表结构差异比对 -> 生成让某一侧"变成对方"的 DDL SQL
  3. 表数据差异比对 -> 生成让某一侧"变成对方"的 DML SQL
本模块只生成 SQL 文本, 绝不替用户执行。
"""
from __future__ import annotations

import datetime
import decimal
import os
import re
import sys
import threading
from dataclasses import dataclass, field

# Oracle Instant Client 本地路径 (thick mode) - Oracle 11g 等旧版需要。
# 打包后 PyInstaller 会把 data 文件放到 sys._MEIPASS / _internal 下，
# 因此运行时同时检查源码目录、临时解包目录和 exe 所在目录。
# 如果没有随包放置 Instant Client，不主动初始化 thick mode，
# 让 python-oracledb 保持默认 thin mode；较新的 Oracle 不需要额外依赖。
_ORACLE_CLIENT_SUBDIR = os.path.join(".oracle_client", "instantclient_21_22")
_oracle_thick_initialized = False


def _runtime_base_dirs():
    dirs = [os.path.dirname(os.path.abspath(__file__))]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(meipass)
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    out = []
    for d in dirs:
        if d and d not in out:
            out.append(d)
    return out


def _oracle_client_dirs():
    return [os.path.join(base, _ORACLE_CLIENT_SUBDIR) for base in _runtime_base_dirs()]


def _init_oracle_thick():
    """首次调用时初始化 python-oracledb thick 模式 (连接 Oracle 11g 等旧版必需)"""
    global _oracle_thick_initialized
    if _oracle_thick_initialized:
        return
    import oracledb
    for lib_dir in _oracle_client_dirs():
        if os.path.isdir(lib_dir):
            try:
                oracledb.init_oracle_client(lib_dir=lib_dir)
                _oracle_thick_initialized = True
                return
            except Exception:
                pass
    # 没有找到随程序携带的 Instant Client 时，不调用 init_oracle_client()。
    # 这样不会因为本机/打包目录缺少 oci.dll 而影响默认 thin mode 连接新版 Oracle。


def parse_connect_url(url):
    """解析 JDBC URL / DSN 字符串 -> profile dict (host/port/服务名或SID)"""
    s = (url or "").strip()
    if not s:
        return {}
    # JDBC: oracle:thin:@//host:port/service (服务名)
    m = re.match(r'jdbc:oracle:thin:@//([^:/]+):(\d+)/(.+?)\s*$', s, re.I)
    if m:
        return {"type": "oracle", "host": m.group(1), "port": int(m.group(2)),
                "ora_mode": "service", "service_name": m.group(3), "sid": ""}
    # JDBC: oracle:thin:@host:port:SID
    m = re.match(r'jdbc:oracle:thin:@([^:/]+):(\d+):(.+?)\s*$', s, re.I)
    if m:
        return {"type": "oracle", "host": m.group(1), "port": int(m.group(2)),
                "ora_mode": "sid", "sid": m.group(3), "service_name": ""}
    # //host:port/service
    m = re.match(r'//([^:/]+):(\d+)/(.+?)\s*$', s)
    if m:
        return {"type": "oracle", "host": m.group(1), "port": int(m.group(2)),
                "ora_mode": "service", "service_name": m.group(3), "sid": ""}
    # host:port:SID (TNS 简写)
    m = re.match(r'([^:/]+):(\d+):(.+?)\s*$', s)
    if m:
        return {"type": "oracle", "host": m.group(1), "port": int(m.group(2)),
                "ora_mode": "sid", "sid": m.group(3), "service_name": ""}
    # host:port/service
    m = re.match(r'([^:/]+):(\d+)/(.+?)\s*$', s)
    if m:
        return {"type": "oracle", "host": m.group(1), "port": int(m.group(2)),
                "ora_mode": "service", "service_name": m.group(3), "sid": ""}
    # mysql://user:pass@host:port/db  或  mysql://host:port/db
    m = re.match(r'mysql://(?:([^:@]+)(?::([^@]*))?@)?([^:/]+):(\d+)/(.+?)\s*$', s, re.I)
    if m:
        return {"type": "mysql", "host": m.group(3), "port": int(m.group(4)),
                "database": m.group(5), "user": m.group(1) or "", "password": m.group(2) or ""}
    # sqlite:///path/to/db
    m = re.match(r'sqlite:///(.+)$', s, re.I)
    if m:
        return {"type": "sqlite", "path": m.group(1)}
    return {}


MAX_ROWS = 200000   # 数据比对单表最大行数(超出报错, 防止内存爆掉)
MAX_SQL = 5000      # 单方向最多输出的 SQL 条数(超出截断并注释说明)
MAX_DETAIL = 200    # 界面展示的差异明细条数

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")


class DBError(Exception):
    """业务错误, 消息可直接展示给用户"""


def check_ident(name: str) -> str:
    n = (name or "").strip()
    if not _IDENT.match(n):
        raise DBError("非法的表名/标识符: %r" % (name,))
    return n


# ---------------------------------------------------------------- 元数据

@dataclass
class Col:
    name: str
    type: str = ""
    nullable: bool = True
    default: str | None = None
    pk: int = 0          # 在主键中的位置(1..n), 0 表示非主键
    comment: str = ""   # 列备注 (Oracle/MySQL)


@dataclass
class IndexMeta:
    """单个索引的元数据"""
    name: str
    cols: list          # 索引列名列表
    unique: bool = False
    # 原始定义, 用于差异展示
    def signature(self):
        return (self.unique, tuple(self.cols))


@dataclass
class TableMeta:
    name: str
    cols: list = field(default_factory=list)   # list[Col]
    indexes: list = field(default_factory=list)  # list[IndexMeta]
    table_comment: str = ""   # 表备注

    @property
    def pk_cols(self):
        return [c.name for c in sorted((c for c in self.cols if c.pk), key=lambda c: c.pk)]

    def col(self, name):
        for c in self.cols:
            if c.name == name:
                return c
        return None

    def index_dict(self):
        return {idx.name: idx for idx in self.indexes}


def norm_type(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().upper())


def norm_default(d):
    if d is None:
        return None
    d = str(d).strip().rstrip(";").strip()
    return d if d else None


# ---------------------------------------------------------------- 连接基类

class BaseDB:
    dialect = "base"

    def __init__(self, conn):
        self.conn = conn
        self.lock = threading.RLock()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def q(self, name: str) -> str:
        """标识符引用方式"""
        return name

    def table_names(self):
        raise NotImplementedError

    def table_meta(self, table: str):
        """返回 TableMeta 或 None(表不存在)"""
        raise NotImplementedError

    def norm_cell(self, v):
        """读取行时的统一处理(LOB 等)"""
        if hasattr(v, "read"):          # Oracle LOB
            try:
                v = v.read()
            except Exception:
                v = None
        return v

    def fetch_rows(self, meta: TableMeta, where=""):
        cols = [c.name for c in meta.cols]
        sql = "SELECT %s FROM %s" % (", ".join(self.q(c) for c in cols), self.q(meta.name))
        w = (where or "").strip()
        if w:
            # 简单防注入 (用户主动输入, 本工具仅供比对)
            if ';' in w or '--' in w:
                raise DBError("where 条件不允许包含分号或注释")
            sql += " WHERE " + w
        pk = meta.pk_cols
        if pk:
            sql += " ORDER BY " + ", ".join(self.q(c) for c in pk)
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(sql)
            out = []
            while True:
                batch = cur.fetchmany(5000)
                if not batch:
                    break
                for r in batch:
                    out.append(tuple(self.norm_cell(v) for v in r))
                    if len(out) > MAX_ROWS:
                        cur.close()
                        raise DBError("表 %s 超过 %d 行, 超出本工具比对范围" % (meta.name, MAX_ROWS))
            try:
                cur.close()
            except Exception:
                pass
        return out


# ---------------------------------------------------------------- Oracle

def _render_oracle_type(dt, dl, dp, ds, cu) -> str:
    dt = (dt or "").upper()
    if dt in ("VARCHAR2", "CHAR", "NVARCHAR2", "NCHAR"):
        suffix = " CHAR" if cu == "C" else ""
        return "%s(%s%s)" % (dt, dl, suffix)
    if dt == "NUMBER":
        if dp is None:
            return "NUMBER"
        if ds in (None, 0):
            return "NUMBER(%d)" % dp
        return "NUMBER(%d,%d)" % (dp, ds)
    if dt == "FLOAT":
        return "FLOAT(%d)" % dp if dp else "FLOAT"
    if dt == "RAW":
        return "RAW(%s)" % dl
    if dt.startswith("TIMESTAMP"):
        return dt          # 自带精度, 如 TIMESTAMP(6)
    return dt


class OracleDB(BaseDB):
    dialect = "oracle"

    def table_names(self):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT table_name FROM user_tables ORDER BY table_name")
            names = [r[0] for r in cur.fetchall()]
            cur.close()
        return names

    def table_meta(self, table: str):
        t = check_ident(table).upper()
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(
                """SELECT column_name, data_type, data_length, data_precision,
                          data_scale, char_used, nullable, data_default
                     FROM user_tab_columns
                    WHERE table_name = :1
                    ORDER BY column_id""", [t])
            rows = cur.fetchall()
            if not rows:
                cur.close()
                return None
            # 列备注
            cur.execute(
                """SELECT column_name, comments
                     FROM user_col_comments
                    WHERE table_name = :1""", [t])
            cmt = {r[0]: (r[1] or "") for r in cur.fetchall()}
            cols = []
            for name, dt, dl, dp, ds, cu, nul, dflt in rows:
                cols.append(Col(
                    name=name,
                    type=_render_oracle_type(dt, dl, dp, ds, cu),
                    nullable=(nul == "Y"),
                    default=norm_default(dflt),
                    comment=cmt.get(name, ""),
                ))
            cur.execute(
                """SELECT cols.column_name, cols.position
                     FROM user_constraints cons
                     JOIN user_cons_columns cols
                       ON cons.constraint_name = cols.constraint_name
                      AND cons.table_name = cols.table_name
                    WHERE cons.constraint_type = 'P' AND cons.table_name = :1
                    ORDER BY cols.position""", [t])
            for i, (cn, _pos) in enumerate(cur.fetchall(), 1):
                c = next((x for x in cols if x.name == cn), None)
                if c:
                    c.pk = i
            # 普通索引 (排除主键)
            indexes = []
            cur.execute(
                """SELECT idx.index_name, idx.uniqueness,
                          col.column_name, col.column_position
                     FROM user_indexes idx
                     JOIN user_ind_columns col
                       ON idx.index_name = col.index_name
                    WHERE idx.table_name = :1
                      AND idx.index_type NOT LIKE '%FUNCTION%'
                      AND NOT EXISTS (
                        SELECT 1 FROM user_constraints c
                         WHERE c.index_name = idx.index_name
                           AND c.constraint_type = 'P')
                    ORDER BY idx.index_name, col.column_position""", [t])
            cur_idx = {}
            for idx_name, uniq, col_name, _pos in cur.fetchall():
                if idx_name not in cur_idx:
                    cur_idx[idx_name] = [idx_name, [], uniq == 'UNIQUE']
                cur_idx[idx_name][1].append(col_name)
            for n, cl, u in cur_idx.values():
                indexes.append(IndexMeta(name=n, cols=cl, unique=u))
            # 表备注
            cur.execute("SELECT comments FROM user_tab_comments WHERE table_name = :1", [t])
            r = cur.fetchone()
            tbl_cmt = (r[0] or "") if r else ""
            cur.close()
        return TableMeta(t, cols, indexes, tbl_cmt)


# ---------------------------------------------------------------- MySQL

class MySQLDB(BaseDB):
    dialect = "mysql"

    def __init__(self, conn, schema):
        super().__init__(conn)
        self.schema = schema

    def q(self, name):
        return "`%s`" % name

    def table_names(self):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(
                """SELECT table_name FROM information_schema.tables
                    WHERE table_schema=%s AND table_type='BASE TABLE'
                    ORDER BY table_name""", (self.schema,))
            names = [r[0] for r in cur.fetchall()]
            cur.close()
        return names

    def table_meta(self, table):
        t = check_ident(table)
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(
                """SELECT column_name, column_type, is_nullable, column_default, extra,
                          column_comment
                     FROM information_schema.columns
                    WHERE table_schema=%s AND table_name=%s
                    ORDER BY ordinal_position""", (self.schema, t))
            rows = cur.fetchall()
            if not rows:
                cur.close()
                return None
            cols = []
            for name, ctype, isnull, dflt, extra, comment in rows:
                cols.append(Col(
                    name=name,
                    type=norm_type(ctype),
                    nullable=(isnull == "YES"),
                    default=_mysql_default(dflt, ctype, extra or ""),
                    comment=comment or "",
                ))
            cur.execute(
                """SELECT column_name FROM information_schema.key_column_usage
                    WHERE table_schema=%s AND table_name=%s AND constraint_name='PRIMARY'
                    ORDER BY ordinal_position""", (self.schema, t))
            for i, (cn,) in enumerate(cur.fetchall(), 1):
                c = next((x for x in cols if x.name == cn), None)
                if c:
                    c.pk = i
            # 普通索引 (排除主键)
            indexes = []
            cur.execute(
                """SELECT index_name, non_unique, column_name, seq_in_index
                     FROM information_schema.statistics
                    WHERE table_schema=%s AND table_name=%s
                      AND index_name <> 'PRIMARY'
                    ORDER BY index_name, seq_in_index""", (self.schema, t))
            cur_idx = {}
            for idx_name, non_uniq, col_name, _seq in cur.fetchall():
                if idx_name not in cur_idx:
                    cur_idx[idx_name] = [idx_name, [], non_uniq == 0]
                cur_idx[idx_name][1].append(col_name)
            for n, cl, u in cur_idx.values():
                indexes.append(IndexMeta(name=n, cols=cl, unique=u))
            # 表备注
            cur.execute(
                "SELECT table_comment FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
                (self.schema, t))
            r = cur.fetchone()
            tbl_cmt = (r[0] or "") if r else ""
            cur.close()
        return TableMeta(t, cols, indexes, tbl_cmt)


def _mysql_default(dflt, ctype, extra):
    if dflt is None:
        return None
    d = str(dflt)
    ct = (ctype or "").lower()
    up = d.upper()
    if up.startswith(("CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME")) or up in ("NULL", "NOW()"):
        return up
    if re.fullmatch(r"-?\d+(\.\d+)?", d):
        return d
    if "default_generated" in extra.lower() and "(" in d:
        return d                       # 表达式默认值
    if ct.startswith(("char", "varchar", "text", "tinytext", "mediumtext", "longtext",
                      "enum", "set", "date", "time", "year", "json")):
        return "'" + d.replace("'", "''") + "'"
    return d


# ---------------------------------------------------------------- SQLite

class SQLiteDB(BaseDB):
    dialect = "sqlite"

    def q(self, name):
        return '"%s"' % name

    def table_names(self):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            names = [r[0] for r in cur.fetchall()]
            cur.close()
        return names

    def table_meta(self, table):
        t = check_ident(table)
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('PRAGMA table_info("%s")' % t)
            rows = cur.fetchall()
            if not rows:
                cur.close()
                return None
            cols = []
            for _cid, name, ctype, notnull, dflt, pk in rows:
                cols.append(Col(
                    name=name,
                    type=norm_type(ctype) if ctype else "TEXT",
                    nullable=(not notnull),
                    default=norm_default(dflt),
                    pk=pk or 0,
                ))
            # 普通索引 (sqlite_master 里的 index 定义, 排除自动索引)
            indexes = []
            cur.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL ORDER BY name",
                (t,))
            for idx_name, idx_sql in cur.fetchall():
                idx_sql = (idx_sql or "").strip()
                unique = bool(re.search(r'\bUNIQUE\b', idx_sql, re.IGNORECASE))
                # 提取列名括号内容
                m = re.search(r'\(([^\)]+)\)', idx_sql)
                idx_cols = []
                if m:
                    for part in m.group(1).split(','):
                        part = part.strip().strip('"').strip()
                        if part:
                            idx_cols.append(part)
                indexes.append(IndexMeta(name=idx_name, cols=idx_cols, unique=unique))
            cur.close()
        return TableMeta(t, cols, indexes, "")


# ---------------------------------------------------------------- 连接工厂

def connect(profile: dict) -> BaseDB:
    """根据连接配置建立连接, 失败抛异常"""
    ptype = (profile.get("type") or "oracle").lower()
    if ptype == "oracle":
        _init_oracle_thick()
        import oracledb
        host = (profile.get("host") or "127.0.0.1").strip()
        port = int(profile.get("port") or 1521)
        mode = profile.get("ora_mode", "service")
        if mode == "sid":
            dsn = oracledb.makedsn(host, port, sid=(profile.get("sid") or "").strip())
        else:
            dsn = oracledb.makedsn(host, port, service_name=(profile.get("service_name") or "").strip())
        kw = dict(user=(profile.get("user") or "").strip(),
                  password=profile.get("password") or "", dsn=dsn)
        try:
            conn = oracledb.connect(tcp_connect_timeout=10, **kw)
        except TypeError:
            conn = oracledb.connect(**kw)
        return OracleDB(conn)
    if ptype == "mysql":
        import pymysql
        conn = pymysql.connect(
            host=(profile.get("host") or "127.0.0.1").strip(),
            port=int(profile.get("port") or 3306),
            user=(profile.get("user") or "").strip(),
            password=profile.get("password") or "",
            database=(profile.get("database") or "").strip(),
            charset="utf8mb4", connect_timeout=10, autocommit=True)
        return MySQLDB(conn, (profile.get("database") or "").strip())
    if ptype == "sqlite":
        import sqlite3
        path = (profile.get("path") or "").strip()
        if not path:
            raise DBError("请填写 SQLite 数据库文件路径")
        conn = sqlite3.connect(path, check_same_thread=False)
        return SQLiteDB(conn)
    raise DBError("不支持的数据库类型: %s" % ptype)


TYPE_NAMES = {"oracle": "Oracle", "mysql": "MySQL", "sqlite": "SQLite"}


# ---------------------------------------------------------------- 结构比对

def col_def(c: Col, dialect: str, for_modify=False, include_nullability=True) -> str:
    q = "`" if dialect == "mysql" else ('"' if dialect == "sqlite" else "")
    name = "%s%s%s" % (q, c.name, q) if q else c.name
    parts = [name, c.type]
    if c.default is not None:
        parts.append("DEFAULT %s" % c.default)
    if include_nullability:
        if not c.nullable:
            parts.append("NOT NULL")
        elif for_modify and dialect in ("oracle", "mysql"):
            parts.append("NULL")
    return " ".join(parts)


def create_table_sql(meta: TableMeta, dialect: str) -> str:
    qn = meta.name if dialect == "oracle" else (("`%s`" % meta.name) if dialect == "mysql" else ('"%s"' % meta.name))
    lines = ["  " + col_def(c, dialect) for c in meta.cols]
    if meta.pk_cols:
        pkq = ", ".join(pk if dialect == "oracle" else (("`%s`" % pk) if dialect == "mysql" else ('"%s"' % pk)) for pk in meta.pk_cols)
        lines.append("  PRIMARY KEY (%s)" % pkq)
    return "CREATE TABLE %s (\n%s\n);" % (qn, ",\n".join(lines))


def _drop_table_sql(name, dialect):
    qn = name if dialect == "oracle" else (("`%s`" % name) if dialect == "mysql" else ('"%s"' % name))
    return "DROP TABLE %s;" % qn


def _sqlite_rebuild_sql(src: TableMeta, dst: TableMeta):
    """SQLite 不支持 MODIFY/改主键, 用重建表方案"""
    t = src.name
    common = [c.name for c in src.cols if dst.col(c.name)]
    cols = ", ".join('"%s"' % c for c in common)
    return [
        'ALTER TABLE "%s" RENAME TO "%s__bak_sync";' % (t, t),
        create_table_sql(src, "sqlite"),
        'INSERT INTO "%s" (%s) SELECT %s FROM "%s__bak_sync";' % (t, cols, cols, t),
        'DROP TABLE "%s__bak_sync";' % t,
    ]


def diff_structure(src: TableMeta | None, dst: TableMeta | None, dialect: str):
    """
    生成在 dst 所在库执行的 SQL, 使其结构变成 src 的样子。
    src 为 None -> 对侧没有这张表 -> dst 需要 DROP TABLE
    dst 为 None -> 本侧没有这张表 -> CREATE TABLE
    """
    if src is None and dst is None:
        return []
    if src is None:
        return ["-- 警告: 该表仅存在于本侧, 对侧没有; 以下语句会删除本侧这张表(请确认后再执行)",
                _drop_table_sql(dst.name, dialect)]
    if dst is None:
        return ["-- 本侧缺少该表, 以下为完整建表语句",
                create_table_sql(src, dialect)]

    src_cols = {c.name: c for c in src.cols}
    dst_cols = {c.name: c for c in dst.cols}
    adds = [c for c in src.cols if c.name not in dst_cols]
    drops = [c for c in dst.cols if c.name not in src_cols]
    mods = []
    for c in src.cols:
        d = dst_cols.get(c.name)
        if d and (norm_type(c.type) != norm_type(d.type)
                  or c.nullable != d.nullable
                  or norm_default(c.default) != norm_default(d.default)):
            mods.append(c)
    pk_changed = src.pk_cols != dst.pk_cols
    t = src.name

    if dialect == "sqlite":
        if mods or pk_changed:
            return ["-- 列定义或主键有差异, SQLite 采用重建表方案"] + _sqlite_rebuild_sql(src, dst)
        out = []
        for c in adds:
            out.append('ALTER TABLE "%s" ADD COLUMN %s;' % (t, col_def(c, "sqlite")))
        for c in drops:
            out.append('ALTER TABLE "%s" DROP COLUMN "%s";' % (t, c.name))
        return out

    stmts = []
    if dialect == "oracle":
        if adds:
            stmts.append("ALTER TABLE %s ADD (\n  %s\n);" % (t, ",\n  ".join(col_def(c, "oracle") for c in adds)))
        for c in mods:
            nullable_changed = c.nullable != dst_cols[c.name].nullable
            stmts.append("ALTER TABLE %s MODIFY (%s);" %
                         (t, col_def(c, "oracle", for_modify=True,
                                     include_nullability=nullable_changed)))
        for c in drops:
            stmts.append("ALTER TABLE %s DROP COLUMN %s;" % (t, c.name))
    else:  # mysql
        for c in adds:
            stmts.append("ALTER TABLE `%s` ADD COLUMN %s;" % (t, col_def(c, "mysql")))
        for c in mods:
            stmts.append("ALTER TABLE `%s` MODIFY COLUMN %s;" % (t, col_def(c, "mysql", for_modify=True)))
        for c in drops:
            stmts.append("ALTER TABLE `%s` DROP COLUMN `%s`;" % (t, c.name))
    if pk_changed:
        if dst.pk_cols:
            stmts.append(_drop_pk_sql(t, dialect))
        if src.pk_cols:
            stmts.append(_add_pk_sql(t, src.pk_cols, dialect))
    # 索引差异 (跳过 SQLite, 因为 SQLite 的索引重建比较麻烦, 留给用户手动)
    if dialect != "sqlite":
        src_idx = {i.name: i for i in src.indexes}
        dst_idx = {i.name: i for i in dst.indexes}
        # 先 DROP 本侧多余的索引
        for n, idx in dst_idx.items():
            if n not in src_idx:
                stmts.append(_drop_index_sql(t, n, dialect))
        # 再 CREATE 本侧缺失的索引 (或重建定义不同的)
        for n, idx in src_idx.items():
            r = dst_idx.get(n)
            if r is None or r.signature() != idx.signature():
                if r is not None:
                    stmts.append(_drop_index_sql(t, n, dialect))
                stmts.append(_create_index_sql(t, idx, dialect))
    else:
        # SQLite: 只处理缺失/多余的索引, 不重建 (避免 SQL 复杂)
        src_idx = {i.name: i for i in src.indexes}
        dst_idx = {i.name: i for i in dst.indexes}
        for n, idx in dst_idx.items():
            if n not in src_idx:
                stmts.append(_drop_index_sql(t, n, dialect))
        for n, idx in src_idx.items():
            if n not in dst_idx:
                stmts.append(_create_index_sql(t, idx, dialect))
    # 表备注 / 列备注 (Oracle/MySQL)
    if dialect == "oracle":
        if (src.table_comment or "") != (dst.table_comment or ""):
            stmts.append("COMMENT ON TABLE %s IS '%s';" % (t, (src.table_comment or "").replace("'", "''")))
        for c in src.cols:
            d = dst_cols.get(c.name)
            src_cmt = (c.comment or "").strip()
            if d is None:
                # 新增列: 直接用 src 的 comment
                if src_cmt:
                    stmts.append("COMMENT ON COLUMN %s.%s IS '%s';" % (t, c.name, src_cmt.replace("'", "''")))
            else:
                dst_cmt = (d.comment or "").strip()
                if src_cmt != dst_cmt:
                    stmts.append("COMMENT ON COLUMN %s.%s IS '%s';" % (t, c.name, src_cmt.replace("'", "''")))
    elif dialect == "mysql":
        if (src.table_comment or "") != (dst.table_comment or ""):
            stmts.append("ALTER TABLE `%s` COMMENT = '%s';" % (t, (src.table_comment or "").replace("'", "''")))
        # MySQL: 列备注和列定义一起 MODIFY
        for c in src.cols:
            d = dst_cols.get(c.name)
            if d and (c.comment or "") != (d.comment or ""):
                # 如果该列未被 MODIFY 检测到, 单独加一条 MODIFY
                if not (norm_type(c.type) != norm_type(d.type)
                        or c.nullable != d.nullable
                        or norm_default(c.default) != norm_default(d.default)):
                    stmts.append("ALTER TABLE `%s` MODIFY COLUMN %s COMMENT '%s';"
                                 % (t, col_def(c, "mysql", for_modify=True),
                                    (c.comment or "").replace("'", "''")))
    return stmts


def _drop_pk_sql(t, dialect):
    if dialect == "oracle":
        return "ALTER TABLE %s DROP PRIMARY KEY;" % t
    return "ALTER TABLE `%s` DROP PRIMARY KEY;" % t


def _add_pk_sql(t, pk_cols, dialect):
    if dialect == "oracle":
        return "ALTER TABLE %s ADD PRIMARY KEY (%s);" % (t, ", ".join(pk_cols))
    return "ALTER TABLE `%s` ADD PRIMARY KEY (%s);" % (t, ", ".join("`%s`" % c for c in pk_cols))


def _drop_index_sql(table, idx_name, dialect):
    if dialect == "oracle":
        return "DROP INDEX %s;" % idx_name
    if dialect == "mysql":
        return "DROP INDEX `%s` ON `%s`;" % (idx_name, table)
    return 'DROP INDEX IF EXISTS "%s";' % idx_name


def _create_index_sql(table, idx: "IndexMeta", dialect: str):
    unique = "UNIQUE " if idx.unique else ""
    if dialect == "oracle":
        cols = ", ".join(idx.cols)
        return "CREATE %sINDEX %s ON %s (%s);" % (unique, idx.name, table, cols)
    if dialect == "mysql":
        cols = ", ".join("`%s`" % c for c in idx.cols)
        return "CREATE %sINDEX `%s` ON `%s` (%s);" % (unique, idx.name, table, cols)
    cols = ", ".join('"%s"' % c for c in idx.cols)
    return 'CREATE %sINDEX "%s" ON "%s" (%s);' % (unique, idx.name, table, cols)


def structure_report(lmeta: TableMeta | None, rmeta: TableMeta | None):
    """返回 (status, [中文差异说明])"""
    if lmeta is None and rmeta is None:
        return "missing_both", ["两侧数据库都不存在该表"]
    if lmeta is None:
        return "only_right", ["仅右侧存在该表, 左侧缺失"]
    if rmeta is None:
        return "only_left", ["仅左侧存在该表, 右侧缺失"]
    details = []
    lcols = {c.name: c for c in lmeta.cols}
    rcols = {c.name: c for c in rmeta.cols}
    for c in lmeta.cols:
        r = rcols.get(c.name)
        if r is None:
            details.append("仅左侧有列 %s (%s)" % (c.name, c.type))
        else:
            if norm_type(c.type) != norm_type(r.type):
                details.append("列 %s 类型不同: 左=%s 右=%s" % (c.name, c.type, r.type))
            if c.nullable != r.nullable:
                details.append("列 %s 可空性不同: 左=%s 右=%s"
                               % (c.name, "可空" if c.nullable else "非空", "可空" if r.nullable else "非空"))
            if norm_default(c.default) != norm_default(r.default):
                details.append("列 %s 默认值不同: 左=%s 右=%s" % (c.name, c.default, r.default))
            if (c.comment or "") != (r.comment or ""):
                details.append("列 %s 备注不同: 左=%s 右=%s" % (c.name, c.comment or "无", r.comment or "无"))
    for c in rmeta.cols:
        if c.name not in lcols:
            details.append("仅右侧有列 %s (%s)" % (c.name, c.type))
    if lmeta.pk_cols != rmeta.pk_cols:
        details.append("主键不同: 左=(%s) 右=(%s)"
                       % (", ".join(lmeta.pk_cols) or "无", ", ".join(rmeta.pk_cols) or "无"))
    # 表备注
    if (lmeta.table_comment or "") != (rmeta.table_comment or ""):
        details.append("表备注不同: 左=%s 右=%s" % (lmeta.table_comment or "无", rmeta.table_comment or "无"))
    # 索引对比
    lidx = {i.name: i for i in lmeta.indexes}
    ridx = {i.name: i for i in rmeta.indexes}
    for n, idx in lidx.items():
        r = ridx.get(n)
        if r is None:
            details.append("仅左侧有索引 %s (%s, 列: %s)"
                           % (n, "唯一" if idx.unique else "非唯一", ", ".join(idx.cols)))
        elif idx.signature() != r.signature():
            details.append("索引 %s 定义不同: 左=(%s, %s) 右=(%s, %s)"
                           % (n, "唯一" if idx.unique else "非唯一", ", ".join(idx.cols),
                              "唯一" if r.unique else "非唯一", ", ".join(r.cols)))
    for n, idx in ridx.items():
        if n not in lidx:
            details.append("仅右侧有索引 %s (%s, 列: %s)"
                           % (n, "唯一" if idx.unique else "非唯一", ", ".join(idx.cols)))
    return ("same" if not details else "diff"), details


# ---------------------------------------------------------------- 数据比对

def norm_val(v):
    """比较用归一化"""
    if v is None:
        return None
    if isinstance(v, str):
        return v.rstrip()
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, decimal.Decimal):
        return v.normalize()
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).hex()
    return v


def literal(v, dialect: str) -> str:
    """将 Python 值渲染为目标方言的 SQL 字面量"""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, decimal.Decimal):
        return format(v, "f")
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, datetime.datetime):
        s = v.strftime("%Y-%m-%d %H:%M:%S") + (".%06d" % v.microsecond if v.microsecond else "")
        if dialect == "oracle":
            if v.microsecond:
                return "TO_TIMESTAMP('%s', 'YYYY-MM-DD HH24:MI:SS.FF6')" % s
            return "TO_DATE('%s', 'YYYY-MM-DD HH24:MI:SS')" % s
        return "'%s'" % s
    if isinstance(v, datetime.date):
        s = v.strftime("%Y-%m-%d")
        return "TO_DATE('%s', 'YYYY-MM-DD')" % s if dialect == "oracle" else "'%s'" % s
    if isinstance(v, (bytes, bytearray)):
        h = bytes(v).hex()
        return "HEXTORAW('%s')" % h if dialect == "oracle" else "X'%s'" % h
    s = str(v).replace("'", "''")
    return "'%s'" % s


def _qname(name, dialect):
    if dialect == "mysql":
        return "`%s`" % name
    if dialect == "sqlite":
        return '"%s"' % name
    return name


def diff_data(lmeta: TableMeta, lrows, rmeta: TableMeta, rrows, dialect: str):
    """
    返回 dict: 两侧修复 SQL + 差异明细
    left_sql  在左侧执行 -> 左侧数据变得与右侧一致
    right_sql 在右侧执行 -> 右侧数据变得与左侧一致
    """
    lcols = [c.name for c in lmeta.cols]
    rcols = [c.name for c in rmeta.cols]
    if {c.upper() for c in lcols} != {c.upper() for c in rcols}:
        raise DBError("表 %s 两侧列不一致, 请先用「同步数据表」对齐结构后再比对数据" % lmeta.name)
    li = {c.upper(): i for i, c in enumerate(lcols)}
    ri = {c.upper(): i for i, c in enumerate(rcols)}
    order = lcols                                   # 以左侧列顺序为准
    pk = lmeta.pk_cols
    no_pk = not pk
    key_cols = pk if pk else order                   # 无主键则整行作为身份标识(只能发现多少行)

    def keyof(row, idx):
        return tuple(norm_val(row[idx[c.upper()]]) for c in key_cols)

    def fullkey(row, idx):
        return tuple(norm_val(row[idx[c.upper()]]) for c in order)

    lmap, rmap = {}, {}
    for r in lrows:
        lmap[keyof(r, li)] = r
    for r in rrows:
        rmap[keyof(r, ri)] = r

    only_left = [k for k in lmap if k not in rmap]
    only_right = [k for k in rmap if k not in lmap]
    changed = []
    if not no_pk:
        for k in lmap:
            if k in rmap and fullkey(lmap[k], li) != fullkey(rmap[k], ri):
                changed.append(k)

    T = _qname(lmeta.name, dialect)
    colq = {d: [_qname(c, d) for c in order] for d in ("oracle", "mysql", "sqlite")}

    def where_pk(row, idx, d):
        parts = []
        for c in key_cols:
            v = row[idx[c.upper()]]
            if v is None:
                parts.append("%s IS NULL" % _qname(c, d))
            else:
                parts.append("%s = %s" % (_qname(c, d), literal(v, d)))
        return " AND ".join(parts)

    def insert_sql(row, idx, d):
        return "INSERT INTO %s (%s) VALUES (%s);" % (
            _qname(lmeta.name, d), ", ".join(colq[d]),
            ", ".join(literal(row[idx[c.upper()]], d) for c in order))

    def delete_sql(row, idx, d):
        return "DELETE FROM %s WHERE %s;" % (_qname(lmeta.name, d), where_pk(row, idx, d))

    def update_sql(src_row, src_idx, key_row, key_idx, d):
        sets = ", ".join("%s = %s" % (_qname(c, d), literal(src_row[src_idx[c.upper()]], d)) for c in order)
        return "UPDATE %s SET %s WHERE %s;" % (_qname(lmeta.name, d), sets, where_pk(key_row, key_idx, d))

    # 左侧修复 SQL: 让左侧 = 右侧
    l_sql = []
    for k in only_right:
        l_sql.append(insert_sql(rmap[k], ri, dialect))
    for k in changed:
        l_sql.append(update_sql(rmap[k], ri, lmap[k], li, dialect))
    for k in only_left:
        l_sql.append(delete_sql(lmap[k], li, dialect))
    # 右侧修复 SQL: 让右侧 = 左侧
    r_sql = []
    for k in only_left:
        r_sql.append(insert_sql(lmap[k], li, dialect))
    for k in changed:
        r_sql.append(update_sql(lmap[k], li, rmap[k], ri, dialect))
    for k in only_right:
        r_sql.append(delete_sql(rmap[k], ri, dialect))

    def capped(sqls):
        if len(sqls) > MAX_SQL:
            return sqls[:MAX_SQL] + ["-- ……差异过多, 仅输出前 %d 条, 共 %d 条" % (MAX_SQL, len(sqls))]
        return sqls

    # 差异明细(界面展示)
    details = []
    for k in only_left[:MAX_DETAIL]:
        details.append({"kind": "only_left", "key": _fmt_key(key_cols, k),
                        "left": _fmt_row(order, lmap[k], li), "right": None, "changed": []})
    for k in only_right[:MAX_DETAIL]:
        details.append({"kind": "only_right", "key": _fmt_key(key_cols, k),
                        "left": None, "right": _fmt_row(order, rmap[k], ri), "changed": []})
    rest = MAX_DETAIL - len(details)
    for k in changed[:max(rest, 0)]:
        ch = [c for c in order
              if norm_val(lmap[k][li[c.upper()]]) != norm_val(rmap[k][ri[c.upper()]])]
        details.append({"kind": "diff", "key": _fmt_key(key_cols, k),
                        "left": _fmt_row(order, lmap[k], li), "right": _fmt_row(order, rmap[k], ri),
                        "changed": ch})

    return {
        "table": lmeta.name,
        "pk": pk,
        "no_pk": no_pk,
        "left_count": len(lrows),
        "right_count": len(rrows),
        "only_left": len(only_left),
        "only_right": len(only_right),
        "updated": len(changed),
        "left_sql": "\n".join(capped(l_sql)),
        "right_sql": "\n".join(capped(r_sql)),
        "details": details,
        "detail_capped": (len(only_left) + len(only_right) + len(changed)) > MAX_DETAIL,
    }


def _fmt_key(cols, key):
    return ", ".join("%s=%s" % (c, v) for c, v in zip(cols, key))


def _fmt_row(order, row, idx):
    out = {}
    for c in order:
        v = row[idx[c.upper()]]
        s = "NULL" if v is None else str(v)
        if len(s) > 100:
            s = s[:100] + "…"
        out[c] = s
    return out
