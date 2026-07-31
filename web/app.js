/* ===== 数据库同步比对工具 - 前端逻辑 ===== */
"use strict";

const SIDES = ["left", "right"];
const S = {
  profiles: [],
  sides: { left: null, right: null },   // {profile_id, name, type, type_name}
  mode: null,                            // 'struct' | 'data'
  busy: false,
};

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(msg, ms) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove("show"), ms || 2200);
}

async function api(method, ...args) {
  const fn = window.pywebview.api[method];
  return await fn(...args);
}

/* ---------------- 表单读写 ---------------- */

function paneOf(side) { return $("#pane-" + side); }

function formProfile(side) {
  const f = paneOf(side);
  const type = $(".f-type", f).value;
  const p = {
    id: f.dataset.pid || "",
    type: type,
    name: $(".f-name", f).value.trim(),
    host: $(".f-hostname", f).value.trim() || "127.0.0.1",
    port: parseInt($(".f-portnum", f).value, 10) || (type === "mysql" ? 3306 : 1521),
    user: $(".f-user", f).value.trim(),
    password: $(".f-pwd", f).value,
    ora_mode: $(".f-ora-mode", f).value,
    service_name: "", sid: "", database: "", path: "",
  };
  if (type === "oracle") {
    if (p.ora_mode === "sid") p.sid = $(".f-ora-value", f).value.trim();
    else p.service_name = $(".f-ora-value", f).value.trim();
  } else if (type === "mysql") {
    p.database = $(".f-database", f).value.trim();
  } else if (type === "sqlite") {
    p.path = $(".f-path", f).value.trim();
  }
  return p;
}

function fillForm(side, p) {
  const f = paneOf(side);
  f.dataset.pid = (p && p.id) || "";
  $(".f-type", f).value = (p && p.type) || "oracle";
  $(".f-name", f).value = (p && p.name) || "";
  $(".f-hostname", f).value = (p && p.host) || "";
  $(".f-portnum", f).value = (p && p.port) || ((p && p.type === "mysql") ? 3306 : 1521);
  $(".f-user", f).value = (p && p.user) || "";
  $(".f-pwd", f).value = (p && p.password) || "";
  $(".f-ora-mode", f).value = (p && p.ora_mode) || "service";
  $(".f-ora-value", f).value = p ? (p.ora_mode === "sid" ? (p.sid || "") : (p.service_name || "")) : "";
  $(".f-database", f).value = (p && p.database) || "";
  $(".f-path", f).value = (p && p.path) || "";
  onTypeChange(side);
  setFormMsg(side, "", "");
}

function setFormMsg(side, msg, cls) {
  const m = $(".form-msg", paneOf(side));
  m.textContent = msg || "";
  m.className = "form-msg " + (cls || "");
}

function onTypeChange(side) {
  const f = paneOf(side);
  const type = $(".f-type", f).value;
  $(".f-ora", f).style.display = type === "oracle" ? "" : "none";
  $(".f-mysql", f).style.display = type === "mysql" ? "" : "none";
  $(".f-sqlite", f).style.display = type === "sqlite" ? "" : "none";
  const showNet = type !== "sqlite";
  $(".f-host-row", f).style.display = showNet ? "" : "none";
  $(".f-userrow", f).style.display = showNet ? "" : "none";
  $(".f-pwdrow", f).style.display = showNet ? "" : "none";
  const port = $(".f-portnum", f);
  if (type === "oracle" && (port.value === "3306" || !port.value)) port.value = "1521";
  if (type === "mysql" && (port.value === "1521" || !port.value)) port.value = "3306";
}

/* ---------------- 渲染 ---------------- */

function otherTypeLocked(side) {
  const other = side === "left" ? "right" : "left";
  return S.sides[other] ? S.sides[other].type : null;
}

function renderSelect(side) {
  const sel = $("#sel-" + side);
  const info = S.sides[side];
  const locked = otherTypeLocked(side);
  // 另一侧已选的 profile_id (避免重复)
  const other = side === "left" ? "right" : "left";
  const otherPid = S.sides[other] ? S.sides[other].profile_id : null;
  sel.innerHTML = "";
  const opt0 = document.createElement("option");
  opt0.value = "";
  opt0.textContent = info ? `${info.name} (${info.type_name})` : "-- 选择已保存的连接 --";
  sel.appendChild(opt0);
  for (const p of S.profiles) {
    if (locked && p.type !== locked) continue;
    if (info && p.id === info.profile_id) continue;
    if (p.id === otherPid) continue;   // 另一侧已选, 在本侧隐藏
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = `${p.name || p.host} (${({ oracle: "Oracle", mysql: "MySQL", sqlite: "SQLite" })[p.type] || p.type})`;
    sel.appendChild(o);
  }
  const onew = document.createElement("option");
  onew.value = "__new__";
  onew.textContent = "＋ 新建数据库链接";
  sel.appendChild(onew);
  sel.value = "";
}

function renderPane(side) {
  const info = S.sides[side];
  const form = $("#form-" + side);
  const ws = $("#ws-" + side);
  $("#dot-" + side).classList.toggle("on", !!info);
  $("#disc-" + side).style.display = info ? "" : "none";
  if (info) {
    form.style.display = "none";
    ws.style.display = "";
  } else {
    form.style.display = "";
    ws.style.display = "none";
    $(".result-area", paneOf(side)).innerHTML = "";
    $(".sql-output", paneOf(side)).value = "";
  }
  renderSelect(side);
}

function renderModes() {
  const both = S.sides.left && S.sides.right;
  $("#btnModeStruct").disabled = !both;
  $("#btnModeData").disabled = !both;
  $("#btnModeStruct").classList.toggle("active", S.mode === "struct");
  $("#btnModeData").classList.toggle("active", S.mode === "data");
  $("#topHint").textContent = both
    ? (S.mode === "struct" ? "模式: 同步数据表(结构比对)" : S.mode === "data" ? "模式: 同步数据(数据比对)" : "两侧已连接, 请选择同步模式")
    : "请先连接两侧数据库";
  for (const side of SIDES) {
    const ws = $("#ws-" + side);
    $(".tool-struct", ws).style.display = S.mode === "struct" ? "" : "none";
    $(".tool-data", ws).style.display = S.mode === "data" ? "" : "none";
  }
}

function renderAll() {
  for (const side of SIDES) renderPane(side);
  renderModes();
}

/* ---------------- 连接动作 ---------------- */

async function doTest(side) {
  const p = formProfile(side);
  setFormMsg(side, "测试中…", "");
  const r = await api("test_profile", p);
  setFormMsg(side, r.msg, r.ok ? "ok" : "err");
}

async function doConnect(side) {
  const p = formProfile(side);
  const remember = $(".f-save", paneOf(side)).checked;
  setFormMsg(side, "连接中…", "");
  setBusy(true);
  try {
    const r = await api("connect", side, p, remember);
    if (!r.ok) { setFormMsg(side, r.msg, "err"); return; }
    applyState(r);
    toast((side === "left" ? "左侧" : "右侧") + "连接成功");
  } finally {
    setBusy(false);
  }
}

async function doSwitch(side, pid) {
  if (pid === "__new__") {
    // 新建连接 -> 回到连接信息页面, 并重置下拉框 value (这样用户再选其他项能触发 change)
    const f = $("#form-" + side);
    fillForm(side, null);
    f.style.display = "";
    $("#ws-" + side).style.display = "none";
    $("#sel-" + side).value = "";
    return;
  }
  const p = S.profiles.find(x => x.id === pid);
  if (!p) { renderSelect(side); return; }
  setBusy(true);
  try {
    const r = await api("connect", side, p, false);
    if (!r.ok) { toast(r.msg || "连接失败"); renderSelect(side); return; }
    applyState(r);
    toast((side === "left" ? "左侧" : "右侧") + "已切换连接");
  } finally {
    setBusy(false);
  }
}

async function doDisconnect(side) {
  const r = await api("disconnect", side);
  applyState(r);
  if (S.mode) { S.mode = null; }
  renderAll();
}

function applyState(r) {
  S.profiles = r.profiles || [];
  S.sides.left = r.left;
  S.sides.right = r.right;
  if (!(S.sides.left && S.sides.right)) S.mode = null;
  renderAll();
}

function setBusy(b) {
  S.busy = b;
  document.body.classList.toggle("loading", b);
}

/* ---------------- 同步数据表(结构) ---------------- */

function parseTables(text) {
  return text.split(/[\s,，;；\n]+/).map(s => s.trim()).filter(Boolean);
}

async function doCompareStruct() {
  const text = $(".table-input", paneOf("left")).value || $(".table-input", paneOf("right")).value;
  const tables = parseTables(text);
  if (!tables.length) { toast("请输入至少一个表名"); return; }
  setBusy(true);
  try {
    const r = await api("compare_structure", tables);
    if (!r.ok) { toast(r.msg || "比对失败", 3500); return; }
    renderStructResults(r);
  } finally {
    setBusy(false);
  }
}

const STATUS_TXT = {
  same: "一致", diff: "有差异", only_left: "仅左侧", only_right: "仅右侧", missing_both: "两侧均无",
};
const STATUS_CLS = {
  same: "st-same", diff: "st-diff", only_left: "st-only", only_right: "st-only", missing_both: "st-missing",
};

function renderStructResults(r) {
  for (const side of SIDES) {
    const area = $(".result-area", paneOf(side));
    if (!r.results.length) { area.innerHTML = '<div class="result-empty">无结果</div>'; continue; }
    area.innerHTML = r.results.map(t => `
      <div class="tcard ${STATUS_CLS[t.status] || ""}">
        <span class="tname">${esc(t.table)}</span>
        <span class="tstatus">${STATUS_TXT[t.status] || t.status}</span>
        ${t.details.length ? "<ul>" + t.details.map(d => `<li>${esc(d)}</li>`).join("") + "</ul>" : ""}
      </div>`).join("");
  }
  $(".sql-output", paneOf("left")).value = r.left_sql;
  $(".sql-output", paneOf("right")).value = r.right_sql;
  const nDiff = r.results.filter(t => t.status !== "same").length;
  toast(nDiff ? `比对完成: ${nDiff} 张表有差异` : "比对完成: 全部一致");
}

/* ---------------- 同步数据 ---------------- */

async function doCompareData() {
  const t = ($(".data-table-input", paneOf("left")).value || $(".data-table-input", paneOf("right")).value).trim();
  if (!t) { toast("请输入表名"); return; }
  setBusy(true);
  try {
    const r = await api("compare_data", t);
    if (!r.ok) { toast(r.msg || "比对失败", 4000); return; }
    renderDataResults(r);
  } finally {
    setBusy(false);
  }
}

function kindTag(kind) {
  if (kind === "only_left") return '<span class="tag tag-only_left">仅左侧</span>';
  if (kind === "only_right") return '<span class="tag tag-only_right">仅右侧</span>';
  return '<span class="tag tag-diff">内容不同</span>';
}

function rowCells(row, changed) {
  if (!row) return "<td></td>";
  return `<td class="drow">${Object.entries(row).map(([k, v]) =>
    `<div class="${changed.includes(k) ? "cell-changed" : ""}">${esc(k)}=${esc(v)}</div>`).join("")}</td>`;
}

function renderDataResults(r) {
  $(".sql-output", paneOf("left")).value = r.left_sql;
  $(".sql-output", paneOf("right")).value = r.right_sql;
  const html = `
    <div class="summary-bar">
      <span>表 <b>${esc(r.table)}</b></span>
      <span class="sg-l">左侧 <b>${r.left_count}</b> 行</span>
      <span class="sg-r">右侧 <b>${r.right_count}</b> 行</span>
      <span>仅左侧 <b>${r.only_left}</b></span>
      <span>仅右侧 <b>${r.only_right}</b></span>
      <span>内容不同 <b>${r.updated}</b></span>
      ${r.no_pk ? '<span style="color:var(--err)">⚠ 无主键, 仅识别多/少行</span>' : ""}
    </div>
    ${r.detail_capped ? '<div style="color:var(--text-dim);font-size:11px;padding-bottom:6px">差异较多, 明细仅展示前 200 条, 完整修复SQL见下方SQL区</div>' : ""}
    ${r.details.length ? `<table class="dtable">
      <thead><tr><th>差异类型</th><th>主键</th><th>左侧数据</th><th>右侧数据</th></tr></thead>
      <tbody>${r.details.map(d => `<tr>
        <td>${kindTag(d.kind)}</td>
        <td class="k">${esc(d.key)}</td>
        ${rowCells(d.left, d.changed)}${rowCells(d.right, d.changed)}
      </tr>`).join("")}</tbody></table>`
      : '<div class="result-empty">两侧数据完全一致</div>'}
    ${r.warn ? `<div style="color:var(--err);font-size:12px;padding-top:6px">${esc(r.warn)}</div>` : ""}`;
  for (const side of SIDES) $(".result-area", paneOf(side)).innerHTML = html;
  const n = r.only_left + r.only_right + r.updated;
  toast(n ? `比对完成: ${n} 行差异` : "比对完成: 数据一致");
}

async function loadTableList() {
  const dl = $("#tables-datalist");
  dl.innerHTML = "";
  for (const side of SIDES) {
    if (!S.sides[side]) continue;
    const r = await api("list_tables", side);
    if (r.ok) {
      dl.innerHTML = r.tables.map(t => `<option value="${esc(t)}">`).join("");
      return;
    }
  }
}

/* ---------------- 复制 ---------------- */

async function copySQL(side) {
  const ta = $(".sql-output", paneOf(side));
  if (!ta.value) { toast("暂无SQL可复制"); return; }
  try {
    await navigator.clipboard.writeText(ta.value);
  } catch (e) {
    ta.select();
    document.execCommand("copy");
  }
  toast("SQL已复制到剪贴板");
}

/* ---------------- 初始化 ---------------- */

function bindPane(side) {
  const f = paneOf(side);
  $(".f-type", f).addEventListener("change", () => onTypeChange(side));
  $(".btn-parse-url", f).addEventListener("click", () => doParseUrl(side));
  $(".f-url", f).addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); doParseUrl(side); } });
  $(".btn-test", f).addEventListener("click", () => doTest(side));
  $(".btn-connect", f).addEventListener("click", () => doConnect(side));
  $("#sel-" + side).addEventListener("change", e => {
    if (e.target.value) doSwitch(side, e.target.value);
  });
  $("#disc-" + side).addEventListener("click", () => doDisconnect(side));
  $(".btn-copy", f).addEventListener("click", () => copySQL(side));
  $(".btn-compare-struct", f).addEventListener("click", doCompareStruct);
  $(".btn-compare-data", f).addEventListener("click", doCompareData);
  // 两侧表名输入保持同步
  $(".table-input", f).addEventListener("input", e => {
    const other = side === "left" ? "right" : "left";
    $(".table-input", paneOf(other)).value = e.target.value;
  });
  $(".data-table-input", f).addEventListener("input", e => {
    const other = side === "left" ? "right" : "left";
    $(".data-table-input", paneOf(other)).value = e.target.value;
  });
  // 两侧 SQL 输出框高度同步
  $(".sql-output", f).addEventListener("mouseup", () => syncSqlHeight(side));
  onTypeChange(side);
}

function syncSqlHeight(side) {
  const src = $(".sql-output", $("#ws-" + side));
  if (!src) return;
  const other = side === "left" ? "right" : "left";
  const dst = $(".sql-output", $("#ws-" + other));
  if (!dst) return;
  // 用 height 同步 (style.height 会覆盖 resize 的结果)
  const h = src.style.height || src.getBoundingClientRect().height + "px";
  dst.style.height = h;
}

function setMode(m) {
  if (!(S.sides.left && S.sides.right)) return;
  // 切换模式时清空两侧的比对结果和 SQL 输出
  if (S.mode !== m) {
    for (const side of SIDES) {
      const ws = $("#ws-" + side);
      const ra = $(".result-area", ws);
      const so = $(".sql-output", ws);
      if (ra) ra.innerHTML = "";
      if (so) so.value = "";
    }
  }
  S.mode = m;
  renderModes();
  if (m === "data") loadTableList();
}

async function init() {
  for (const side of SIDES) bindPane(side);
  $("#btnModeStruct").addEventListener("click", () => setMode("struct"));
  $("#btnModeData").addEventListener("click", () => setMode("data"));
  const st = await api("get_state");
  applyState(st);
}

/* ---------------- URL 解析粘贴 ---------------- */

async function doParseUrl(side) {
  const f = paneOf(side);
  const url = $(".f-url", f).value.trim();
  if (!url) { toast("请粘贴 JDBC URL 或 DSN 字符串"); return; }
  try {
    const r = await api("parse_url", url);
    if (!r.ok) { toast(r.msg || "解析失败"); return; }
    const p = r.profile;
    if (!p || !p.type) { toast("无法识别该字符串"); return; }
    // 类型切换后会触发 onTypeChange 调整可见字段
    $(".f-type", f).value = p.type;
    if (p.host) $(".f-hostname", f).value = p.host;
    if (p.port) $(".f-portnum", f).value = String(p.port);
    if (p.user) $(".f-user", f).value = p.user;
    // 密码不填 (URL 里没密码时不动)
    if (p.ora_mode) {
      $(".f-ora-mode", f).value = p.ora_mode;
      $(".f-ora-value", f).value = p.ora_mode === "sid" ? (p.sid || "") : (p.service_name || "");
    }
    if (p.database) $(".f-database", f).value = p.database;
    if (p.path) $(".f-path", f).value = p.path;
    onTypeChange(side);
    toast("已填充: " + (p.type === "oracle" ? (p.ora_mode === "sid" ? p.sid : p.service_name) : (p.host || "")));
  } catch (e) {
    toast("解析出错: " + e);
  }
}

window.addEventListener("pywebviewready", init);
