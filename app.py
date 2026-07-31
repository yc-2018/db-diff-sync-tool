# -*- coding: utf-8 -*-
"""
数据库同步比对工具 - 应用后端
窗口: pywebview (原生窗口 + 内嵌网页 UI)
数据库: Oracle 优先, 兼容 MySQL / SQLite
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

import dbcore

BASE_DIR = Path(__file__).resolve().parent
STORE_DIR = Path.home() / ".dbsync_tool"
STORE_FILE = STORE_DIR / "connections.json"


# ------------------------------------------------------------ 配置持久化

def _enc(s):
    return base64.b64encode((s or "").encode("utf-8")).decode("ascii")


def _dec(s):
    try:
        return base64.b64decode((s or "").encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def load_profiles():
    try:
        data = json.loads(STORE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for p in data if isinstance(data, list) else []:
        p = dict(p)
        p["password"] = _dec(p.pop("password_enc", ""))
        out.append(p)
    return out


def save_profiles(profiles):
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    data = []
    for p in profiles:
        q = dict(p)
        q["password_enc"] = _enc(q.pop("password", ""))
        data.append(q)
    STORE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_profile(p):
    profiles = load_profiles()
    if not p.get("id"):
        p["id"] = uuid.uuid4().hex[:12]
    for i, q in enumerate(profiles):
        if q.get("id") == p["id"]:
            profiles[i] = p
            break
    else:
        profiles.append(p)
    save_profiles(profiles)
    return p


# ------------------------------------------------------------ JS API

class Api:
    def __init__(self):
        self._mu = threading.Lock()
        self._sides = {"left": None, "right": None}   # {"profile": p, "db": BaseDB}

    # ---- 内部工具 ----
    def _side_info(self, side):
        s = self._sides[side]
        if not s:
            return None
        p = s["profile"]
        return {"profile_id": p.get("id"), "name": p.get("name") or p.get("host"),
                "type": p.get("type"), "type_name": dbcore.TYPE_NAMES.get(p.get("type"), p.get("type"))}

    def _state(self):
        return {"ok": True,
                "profiles": load_profiles(),
                "left": self._side_info("left"),
                "right": self._side_info("right")}

    def _require(self, side):
        s = self._sides[side]
        if not s:
            raise dbcore.DBError("%s尚未连接数据库" % ("左侧" if side == "left" else "右侧"))
        return s

    # ---- 连接管理 ----
    def get_state(self):
        return self._state()

    def parse_url(self, url):
        """解析 JDBC URL / DSN -> profile dict, 供前端粘贴 URL 自动填充"""
        try:
            return {"ok": True, "profile": dbcore.parse_connect_url(url)}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def test_profile(self, p):
        try:
            db = dbcore.connect(p)
            db.close()
            return {"ok": True, "msg": "连接成功"}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def connect(self, side, p, remember=True):
        if side not in ("left", "right"):
            return {"ok": False, "msg": "非法侧: %s" % side}
        other = "right" if side == "left" else "left"
        with self._mu:
            try:
                if self._sides[other] and self._sides[other]["profile"].get("type") != p.get("type"):
                    return {"ok": False,
                            "msg": "两侧必须连接相同类型的数据库(当前另一侧是 %s)"
                                   % dbcore.TYPE_NAMES.get(self._sides[other]["profile"].get("type"))}
                db = dbcore.connect(p)
            except Exception as e:
                return {"ok": False, "msg": "连接失败: %s" % e}
            old = self._sides[side]
            if old:
                old["db"].close()
            if remember:
                p = upsert_profile(dict(p))
            self._sides[side] = {"profile": p, "db": db}
        return self._state()

    def disconnect(self, side):
        with self._mu:
            old = self._sides[side]
            if old:
                old["db"].close()
            self._sides[side] = None
        return self._state()

    def delete_profile(self, pid):
        profiles = [q for q in load_profiles() if q.get("id") != pid]
        save_profiles(profiles)
        return {"ok": True}

    def list_tables(self, side):
        try:
            s = self._require(side)
            return {"ok": True, "tables": s["db"].table_names()}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    # ---- 结构比对 ----
    def compare_structure(self, tables):
        try:
            L = self._require("left")
            R = self._require("right")
            dialect = L["db"].dialect
            if not isinstance(tables, list) or not tables:
                raise dbcore.DBError("请至少输入一个表名")
            results, l_all, r_all = [], [], []
            for t in tables:
                t = (t or "").strip()
                if not t:
                    continue
                if dialect == "oracle":
                    t = t.upper()
                lm = L["db"].table_meta(t)
                rm = R["db"].table_meta(t)
                status, details = dbcore.structure_report(lm, rm)
                results.append({"table": t, "status": status, "details": details})
                if status == "missing_both":
                    continue
                l_sql = dbcore.diff_structure(rm, lm, dialect)   # 让左侧变成右侧
                r_sql = dbcore.diff_structure(lm, rm, dialect)   # 让右侧变成左侧
                if l_sql:
                    l_all.append("-- -------- 表 %s --------" % t)
                    l_all.extend(l_sql)
                if r_sql:
                    r_all.append("-- -------- 表 %s --------" % t)
                    r_all.extend(r_sql)
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            l_head = ["-- ========================================================",
                      "-- 表结构修复SQL: 在【左侧库】执行后, 左侧结构将与右侧一致",
                      "-- 生成时间: %s    共 %d 张表" % (now, len(results)),
                      "-- 注意: 应用不会替你执行, 请复制到你的数据库工具中确认后执行",
                      "-- ========================================================"]
            r_head = ["-- ========================================================",
                      "-- 表结构修复SQL: 在【右侧库】执行后, 右侧结构将与左侧一致",
                      "-- 生成时间: %s    共 %d 张表" % (now, len(results)),
                      "-- 注意: 应用不会替你执行, 请复制到你的数据库工具中确认后执行",
                      "-- ========================================================"]
            return {"ok": True, "dialect": dialect, "results": results,
                    "left_sql": "\n".join(l_head) + "\n\n" + "\n\n".join(l_all) if l_all else "\n".join(l_head) + "\n\n-- 无差异, 无需执行",
                    "right_sql": "\n".join(r_head) + "\n\n" + "\n\n".join(r_all) if r_all else "\n".join(r_head) + "\n\n-- 无差异, 无需执行"}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    # ---- 数据比对 ----
    def compare_data(self, table):
        try:
            L = self._require("left")
            R = self._require("right")
            dialect = L["db"].dialect
            t = (table or "").strip()
            if not t:
                raise dbcore.DBError("请输入表名")
            if dialect == "oracle":
                t = t.upper()
            lm = L["db"].table_meta(t)
            rm = R["db"].table_meta(t)
            if lm is None and rm is None:
                raise dbcore.DBError("两侧数据库都不存在表 %s" % t)
            if lm is None:
                raise dbcore.DBError("左侧不存在表 %s, 无法进行数据比对(可先做结构同步)" % t)
            if rm is None:
                raise dbcore.DBError("右侧不存在表 %s, 无法进行数据比对(可先做结构同步)" % t)
            lrows = L["db"].fetch_rows(lm)
            rrows = R["db"].fetch_rows(rm)
            diff = dbcore.diff_data(lm, lrows, rm, rrows, dialect)
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            l_head = ["-- ========================================================",
                      "-- 数据修复SQL: 在【左侧库】执行后, 左侧数据将与右侧一致",
                      "-- 表: %s    生成时间: %s" % (t, now),
                      "-- 共 %d 条: 新增 %d / 更新 %d / 删除 %d" % (
                          diff["only_right"] + diff["updated"] + diff["only_left"],
                          diff["only_right"], diff["updated"], diff["only_left"]),
                      "-- 注意: 应用不会替你执行, 请复制到你的数据库工具中确认后执行",
                      "-- ========================================================"]
            r_head = ["-- ========================================================",
                      "-- 数据修复SQL: 在【右侧库】执行后, 右侧数据将与左侧一致",
                      "-- 表: %s    生成时间: %s" % (t, now),
                      "-- 共 %d 条: 新增 %d / 更新 %d / 删除 %d" % (
                          diff["only_left"] + diff["updated"] + diff["only_right"],
                          diff["only_left"], diff["updated"], diff["only_right"]),
                      "-- 注意: 应用不会替你执行, 请复制到你的数据库工具中确认后执行",
                      "-- ========================================================"]
            diff["ok"] = True
            diff["dialect"] = dialect
            diff["left_sql"] = "\n".join(l_head) + "\n\n" + (diff["left_sql"] or "-- 无差异, 无需执行")
            diff["right_sql"] = "\n".join(r_head) + "\n\n" + (diff["right_sql"] or "-- 无差异, 无需执行")
            if diff["no_pk"]:
                diff["warn"] = "该表没有主键, 只能按整行比对出「多/少行」, 无法识别「内容修改」"
            return diff
        except Exception as e:
            return {"ok": False, "msg": str(e)}


# ------------------------------------------------------------ 入口

def main():
    import webview
    api = Api()
    html = str(BASE_DIR / "web" / "index.html")
    win = webview.create_window(
        "数据库同步比对工具", html, js_api=api,
        width=1320, height=860, min_size=(1080, 720),
        text_select=True)
    if os.environ.get("DBSYNC_SMOKE"):
        def closer():
            time.sleep(6)
            try:
                win.destroy()
            except Exception:
                pass
        threading.Thread(target=closer, daemon=True).start()
    webview.start(debug=False)


if __name__ == "__main__":
    sys.exit(main())
