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
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

import dbcore

BASE_DIR = Path(__file__).resolve().parent
APP_VERSION = "2.0.11"
APP_TITLE = "数据库对比工具 v%s" % APP_VERSION
APP_ICON = BASE_DIR / "web" / "app-icon.ico"
STORE_DIR = Path.home() / ".dbsync_tool"
STORE_FILE = STORE_DIR / "connections.json"
SESSION_FILE = STORE_DIR / "session.json"
WEBVIEW_STORAGE_DIR = STORE_DIR / "webview"
WEBVIEW_ASSET_CACHE_DIRS = (
    Path("EBWebView") / "Default" / "Cache",
    Path("EBWebView") / "Default" / "Code Cache",
    Path("EBWebView") / "Default" / "Service Worker" / "CacheStorage",
)
UNBLOCK_EXTENSIONS = {".dll", ".exe", ".pyd"}


def clear_webview_asset_cache(storage_dir=WEBVIEW_STORAGE_DIR):
    """清理网页资源缓存，同时保留 Local Storage 中的比对历史。"""
    root = Path(storage_dir).resolve()
    for relative in WEBVIEW_ASSET_CACHE_DIRS:
        target = (root / relative).resolve()
        if root not in target.parents:
            continue
        try:
            shutil.rmtree(target)
        except FileNotFoundError:
            pass
        except OSError:
            # 同一工具已有窗口运行时缓存文件可能被占用，不阻止新窗口启动。
            pass


def unblock_bundled_runtime_files():
    """移除下载 ZIP 解压后可能附带的 Windows Internet Zone 标记。"""
    if not getattr(sys, "frozen", False):
        return
    try:
        root = Path(sys.executable).resolve().parent
    except OSError:
        return
    try:
        candidates = [root, root / "_internal"]
        for base in candidates:
            if not base.is_dir():
                continue
            for path in base.rglob("*"):
                if path.suffix.lower() not in UNBLOCK_EXTENSIONS:
                    continue
                try:
                    os.remove(str(path) + ":Zone.Identifier")
                except OSError:
                    pass
    except OSError:
        pass


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


def load_session():
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_session(sess):
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(sess, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_profile(p, require_name=False):
    """校验连接配置；测试连接不要求配置名，保存时要求。"""
    if not isinstance(p, dict):
        raise dbcore.DBError("连接配置无效")
    if require_name and not (p.get("name") or "").strip():
        raise dbcore.DBError("请填写配置名")

    ptype = (p.get("type") or "").strip().lower()
    if ptype not in dbcore.TYPE_NAMES:
        raise dbcore.DBError("请选择数据库类型")
    if ptype == "sqlite":
        if not (p.get("path") or "").strip():
            raise dbcore.DBError("请填写 SQLite 数据库文件路径")
        return

    if not (p.get("host") or "").strip():
        raise dbcore.DBError("请填写主机")
    try:
        port = int(str(p.get("port") or "").strip())
    except (TypeError, ValueError):
        raise dbcore.DBError("请输入有效端口")
    if not 1 <= port <= 65535:
        raise dbcore.DBError("端口必须在 1 到 65535 之间")
    if not (p.get("user") or "").strip():
        raise dbcore.DBError("请填写用户名")

    if ptype == "oracle":
        mode = (p.get("ora_mode") or "service").strip().lower()
        if mode not in ("service", "sid"):
            raise dbcore.DBError("请选择 Oracle 连接方式")
        key = "sid" if mode == "sid" else "service_name"
        if not (p.get(key) or "").strip():
            raise dbcore.DBError("请填写 Oracle %s" % ("SID" if mode == "sid" else "服务名"))
    elif not (p.get("database") or "").strip():
        raise dbcore.DBError("请填写 MySQL 数据库名")


def upsert_profile(p):
    validate_profile(p, require_name=True)
    profiles = load_profiles()
    name = (p.get("name") or "").strip()
    if name:
        for q in profiles:
            if q.get("id") != p.get("id") and (q.get("name") or "").strip() == name:
                raise dbcore.DBError("配置名「%s」已存在, 请使用其他名称" % name)
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
                "type": p.get("type"), "type_name": dbcore.TYPE_NAMES.get(p.get("type"), p.get("type")),
                "tag": p.get("tag", "")}

    def _state(self):
        sess = load_session()
        return {"ok": True,
                "profiles": load_profiles(),
                "left": self._side_info("left"),
                "right": self._side_info("right"),
                "last_left": sess.get("last_left", ""),
                "last_right": sess.get("last_right", "")}

    def _require(self, side):
        s = self._sides[side]
        if not s:
            raise dbcore.DBError("%s尚未连接数据库" % ("左侧" if side == "left" else "右侧"))
        return s

    # ---- 连接管理 ----
    def get_state(self):
        return self._state()

    def restore_connect(self, side):
        """启动时自动恢复上次连接 (按 session.json 中记录的 profile id)"""
        try:
            sess = load_session()
            key = "last_left" if side == "left" else "last_right"
            pid = sess.get(key, "")
            if not pid:
                return self._state()
            profiles = load_profiles()
            p = None
            for q in profiles:
                if q.get("id") == pid:
                    p = dict(q)   # load_profiles 已把 password_enc 解密为 password
                    break
            if not p:
                # profile 已被删除, 清除 session 记录
                del sess[key]
                save_session(sess)
                return self._state()
            # 类型检查
            other = "right" if side == "left" else "left"
            if self._sides[other] and self._sides[other]["profile"].get("type") != p.get("type"):
                return self._state()  # 类型不匹配, 不恢复
            try:
                validate_profile(p, require_name=True)
                db = dbcore.connect(p)
            except Exception:
                # 恢复失败 (如网络不通), 不报错, 仅清除 session
                del sess[key]
                save_session(sess)
                return self._state()
            with self._mu:
                old = self._sides[side]
                if old:
                    old["db"].close()
                self._sides[side] = {"profile": p, "db": db}
            return self._state()
        except Exception:
            return self._state()

    def parse_url(self, url):
        """解析 JDBC URL / DSN -> profile dict, 供前端粘贴 URL 自动填充"""
        try:
            return {"ok": True, "profile": dbcore.parse_connect_url(url)}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def test_profile(self, p):
        try:
            validate_profile(p)
            db = dbcore.connect(p)
            db.close()
            return {"ok": True, "msg": "连接成功"}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def save_profile(self, p):
        """仅保存连接配置，不访问数据库；编辑时密码留空则保留原密码。"""
        try:
            p = dict(p or {})
            if p.get("id") and not p.get("password"):
                old = next((q for q in load_profiles() if q.get("id") == p["id"]), None)
                if old:
                    p["password"] = old.get("password", "")
            saved = upsert_profile(p)
            state = self._state()
            state["profile"] = saved
            state["msg"] = "配置已保存"
            return state
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def connect(self, side, p, remember=True):
        if side not in ("left", "right"):
            return {"ok": False, "msg": "非法侧: %s" % side}
        other = "right" if side == "left" else "left"
        with self._mu:
            try:
                validate_profile(p, require_name=True)
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
        # 记住本次连接
        sess = load_session()
        sess["last_left" if side == "left" else "last_right"] = p.get("id", "")
        save_session(sess)
        return self._state()

    def disconnect(self, side):
        with self._mu:
            old = self._sides[side]
            if old:
                old["db"].close()
            self._sides[side] = None
        sess = load_session()
        key = "last_left" if side == "left" else "last_right"
        if key in sess:
            del sess[key]
            save_session(sess)
        return self._state()

    def delete_profile(self, pid):
        try:
            pid = (pid or "").strip()
            if not pid:
                return {"ok": False, "msg": "缺少配置 id"}
            profiles = load_profiles()
            if not any(q.get("id") == pid for q in profiles):
                return {"ok": False, "msg": "找不到该配置"}
            save_profiles([q for q in profiles if q.get("id") != pid])

            sess = load_session()
            with self._mu:
                for side, key in (("left", "last_left"), ("right", "last_right")):
                    old = self._sides[side]
                    if old and old["profile"].get("id") == pid:
                        try:
                            old["db"].close()
                        except Exception:
                            pass
                        self._sides[side] = None
                    if sess.get(key) == pid:
                        del sess[key]
            save_session(sess)
            return self._state()
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def update_profile(self, p):
        """更新已保存的数据源配置 (编辑)"""
        try:
            if not p.get("id"):
                return {"ok": False, "msg": "缺少配置 id"}
            profiles = load_profiles()
            # 名称唯一校验
            name = (p.get("name") or "").strip()
            if name:
                for q in profiles:
                    if q.get("id") != p["id"] and (q.get("name") or "").strip() == name:
                        return {"ok": False, "msg": "配置名「%s」已存在, 请使用其他名称" % name}
            found = False
            for i, q in enumerate(profiles):
                if q.get("id") == p["id"]:
                    # 保留原密码如果新密码为空
                    if not p.get("password"):
                        p["password"] = q.get("password", "")
                    validate_profile(p, require_name=True)
                    profiles[i] = p
                    found = True
                    break
            if not found:
                return {"ok": False, "msg": "找不到该配置"}
            save_profiles(profiles)
            return self._state()
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def list_tables(self, side):
        try:
            s = self._require(side)
            return {"ok": True, "tables": s["db"].table_names()}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def refresh(self, side):
        """刷新指定侧: 检查连接是否存活, 返回最新状态 (表列表由前端调用 list_tables 独立获取)"""
        try:
            s = self._require(side)
            # Oracle/MySQL: 用 SELECT 1 探活; SQLite: table_names 取一下
            try:
                cur = s["db"].conn.cursor()
                cur.execute("SELECT 1 FROM dual" if s["db"].dialect == "oracle" else "SELECT 1")
                cur.fetchone()
                cur.close()
            except Exception:
                # 探活失败则断开
                with self._mu:
                    old = self._sides[side]
                    if old:
                        try: old["db"].close()
                        except Exception: pass
                    self._sides[side] = None
                return self._state()
            # 清空工作区结果 (前端会被 renderAll 清空)
            return self._state()
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
                results.append({
                    "table": t,
                    "status": status,
                    "details": details,
                    "detail_sides": [dbcore.structure_detail_side(d) for d in details],
                })
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
    def _data_compare_context(self, table, where):
        L = self._require("left")
        R = self._require("right")
        dialect = L["db"].dialect
        t = (table or "").strip()
        if not t:
            raise dbcore.DBError("请输入表名")
        if dialect == "oracle":
            t = t.upper()
        w = (where or "").strip()
        if w:
            lw = w.lower()
            if lw.startswith("where "):
                w = w[6:].strip()
            elif lw.startswith("where"):
                w = w[5:].strip()
        lm = L["db"].table_meta(t)
        rm = R["db"].table_meta(t)
        if lm is None and rm is None:
            raise dbcore.DBError("两侧数据库都不存在表 %s" % t)
        if lm is None:
            raise dbcore.DBError("左侧不存在表 %s, 无法进行数据比对(可先做结构对比)" % t)
        if rm is None:
            raise dbcore.DBError("右侧不存在表 %s, 无法进行数据比对(可先做结构对比)" % t)
        return L, R, dialect, t, w, lm, rm

    def preview_data_compare(self, table, where=""):
        try:
            L, R, _dialect, t, w, lm, rm = self._data_compare_context(table, where)
            try:
                left_count = L["db"].count_rows(lm, where=w)
            except Exception as e:
                action = "WHERE 条件执行失败" if w else "数据行数统计失败"
                raise dbcore.DBError("左侧数据库 %s: %s" % (action, e)) from e
            try:
                right_count = R["db"].count_rows(rm, where=w)
            except Exception as e:
                action = "WHERE 条件执行失败" if w else "数据行数统计失败"
                raise dbcore.DBError("右侧数据库 %s: %s" % (action, e)) from e
            return {
                "ok": True,
                "table": t,
                "left_count": left_count,
                "right_count": right_count,
                "warning_threshold": dbcore.ROW_WARNING_THRESHOLD,
                "requires_confirmation": max(left_count, right_count) > dbcore.ROW_WARNING_THRESHOLD,
            }
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def compare_data(self, table, where=""):
        try:
            L, R, dialect, t, w, lm, rm = self._data_compare_context(table, where)
            try:
                lrows = L["db"].fetch_rows(lm, where=w)
            except Exception as e:
                raise dbcore.DBError("左侧数据库读取数据失败: %s" % e) from e
            try:
                rrows = R["db"].fetch_rows(rm, where=w)
            except Exception as e:
                raise dbcore.DBError("右侧数据库读取数据失败: %s" % e) from e
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
    unblock_bundled_runtime_files()
    import webview
    clear_webview_asset_cache()
    WEBVIEW_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    api = Api()
    html = str(BASE_DIR / "web" / "index.html")
    win = webview.create_window(
        APP_TITLE, html, js_api=api,
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
    webview.start(
        debug=False,
        private_mode=False,
        storage_path=str(WEBVIEW_STORAGE_DIR),
        icon=str(APP_ICON),
    )


if __name__ == "__main__":
    sys.exit(main())
