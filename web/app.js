/* ===== 数据库同步比对工具 - 前端逻辑 ===== */
"use strict";

const SIDES = ["left", "right"];
const S = {
  profiles: [],
  sides: { left: null, right: null },   // {profile_id, name, type, type_name}
  tables: { left: [], right: [] },
  tableListRequest: 0,
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
  const rawPort = $(".f-portnum", f).value.trim();
  // 标签: 从 radio 组读取
  let tag = "";
  const radios = f.querySelectorAll('input[name="tag-' + side + '"]');
  for (const r of radios) { if (r.checked) { tag = r.value; break; } }
  const p = {
    id: f.dataset.pid || "",
    type: type,
    name: $(".f-name", f).value.trim(),
    tag: tag,
    host: $(".f-hostname", f).value.trim(),
    port: /^\d+$/.test(rawPort) ? Number(rawPort) : rawPort,
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

function profileValidationError(p, requireName) {
  if (requireName && !p.name) return "请填写配置名";
  if (p.type === "sqlite") return p.path ? "" : "请填写 SQLite 数据库文件路径";
  if (!p.host) return "请填写主机";
  if (!Number.isInteger(p.port) || p.port < 1 || p.port > 65535) return "请输入 1 到 65535 之间的有效端口";
  if (!p.user) return "请填写用户名";
  if (p.type === "oracle") {
    if (p.ora_mode === "sid" && !p.sid) return "请填写 Oracle SID";
    if (p.ora_mode !== "sid" && !p.service_name) return "请填写 Oracle 服务名";
  }
  if (p.type === "mysql" && !p.database) return "请填写 MySQL 数据库名";
  return "";
}

function fillForm(side, p) {
  const f = paneOf(side);
  f.dataset.pid = (p && p.id) || "";
  $(".f-type", f).value = (p && p.type) || "oracle";
  $(".f-name", f).value = (p && p.name) || "";
  // 标签 radio
  const tagVal = (p && p.tag) || "";
  const radios = f.querySelectorAll('input[name="tag-' + side + '"]');
  for (const r of radios) { r.checked = (r.value === tagVal); }
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
  // 兼容: 渲染自定义下拉列表
  const dd = $("#dd-" + side);
  const display = $("#dd-label-" + side);
  const list = $("#dd-list-" + side);
  const info = S.sides[side];
  const locked = otherTypeLocked(side);
  const other = side === "left" ? "right" : "left";
  const otherPid = S.sides[other] ? S.sides[other].profile_id : null;
  // 显示文本
  if (info) {
    const typeTxt = ({ oracle: "Oracle", mysql: "MySQL", sqlite: "SQLite" })[info.type] || info.type;
    const tagHtml = info.tag === "test" ? '<span class="tag-badge tag-test">测试</span>'
      : info.tag === "prod" ? '<span class="tag-badge tag-prod">正式</span>'
      : info.tag === "dev" ? '<span class="tag-badge tag-dev">开发</span>' : "";
    display.innerHTML = `${esc(info.name)} (${typeTxt})${tagHtml}`;
  } else {
    display.textContent = "-- 选择已保存的连接 --";
  }
  // 列表项
  list.innerHTML = "";
  for (const p of S.profiles) {
    if (locked && p.type !== locked) continue;
    if (info && p.id === info.profile_id) continue;
    if (p.id === otherPid) continue;
    const item = document.createElement("div");
    item.className = "dd-item";
    item.dataset.pid = p.id;
    const typeTxt = ({ oracle: "Oracle", mysql: "MySQL", sqlite: "SQLite" })[p.type] || p.type;
    const tagHtml = p.tag === "test" ? '<span class="tag-badge tag-test">测试</span>'
      : p.tag === "prod" ? '<span class="tag-badge tag-prod">正式</span>'
      : p.tag === "dev" ? '<span class="tag-badge tag-dev">开发</span>' : "";
    item.innerHTML = `<span class="dd-name">${esc(p.name || p.host)}</span><span class="dd-type">${typeTxt}</span>${tagHtml}<button class="dd-edit" data-pid="${esc(p.id)}">编辑</button><button class="dd-delete" data-pid="${esc(p.id)}">删除</button>`;
    item.addEventListener("click", e => {
      // 点操作按钮不触发切换
      if (e.target.closest(".dd-edit, .dd-delete")) return;
      dd.classList.remove("open");
      doSwitch(side, p.id);
    });
    list.appendChild(item);
  }
  // 新建选项
  const onew = document.createElement("div");
  onew.className = "dd-item dd-new";
  onew.textContent = "＋ 新建数据库链接";
  onew.addEventListener("click", () => {
    dd.classList.remove("open");
    doSwitch(side, "__new__");
  });
  list.appendChild(onew);
}

function renderPane(side) {
  const info = S.sides[side];
  const form = $("#form-" + side);
  const ws = $("#ws-" + side);
  $("#dot-" + side).classList.toggle("on", !!info);
  $("#disc-" + side).style.display = info ? "" : "none";
  $("#refresh-" + side).style.display = info ? "" : "none";
  $("#edit-" + side).style.display = info ? "" : "none";
  $("#del-" + side).style.display = info ? "" : "none";
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
  $("#shared-struct-picker").style.display = S.mode === "struct" ? "" : "none";
  $("#shared-data-picker").style.display = S.mode === "data" ? "" : "none";
  $("#shared-where").style.display = S.mode === "data" ? "" : "none";
  const submit = $("#btnCompareShared");
  submit.disabled = !both || !S.mode;
  submit.textContent = S.mode === "data" ? "开始比对数据" : "开始比对结构";
}

function renderAll() {
  for (const side of SIDES) renderPane(side);
  renderModes();
}

/* ---------------- 连接动作 ---------------- */

async function doTest(side) {
  const p = formProfile(side);
  const validationError = profileValidationError(p, false);
  if (validationError) {
    setFormMsg(side, validationError, "err");
    return;
  }
  setFormMsg(side, "测试中…", "");
  try {
    const r = await api("test_profile", p);
    setFormMsg(side, r.msg, r.ok ? "ok" : "err");
  } catch (e) {
    setFormMsg(side, "测试失败: " + e, "err");
  }
}

function profileNameError(p) {
  const name = (p.name || "").trim();
  if (!name) return "";
  for (const q of S.profiles) {
    if (q.id !== p.id && (q.name || "").trim() === name) {
      return "配置名「" + name + "」已存在, 请使用其他名称";
    }
  }
  return "";
}

async function persistProfile(side, p) {
  const validationError = profileValidationError(p, true);
  if (validationError) {
    setFormMsg(side, validationError, "err");
    return null;
  }
  const nameError = profileNameError(p);
  if (nameError) {
    setFormMsg(side, nameError, "err");
    return null;
  }
  const r = await api("save_profile", p);
  if (!r.ok) {
    setFormMsg(side, r.msg || "保存失败", "err");
    return null;
  }
  applyState(r);
  const saved = r.profile || p;
  paneOf(side).dataset.pid = saved.id || p.id || "";
  return saved;
}

function closeConnectionForm(side) {
  const pane = paneOf(side);
  const f = $("#form-" + side);
  f.classList.remove("modal-mode");
  const btnCancel = $(".btn-cancel-edit", pane);
  if (btnCancel) btnCancel.style.display = "none";
  delete pane._editBackup;
  renderPane(side);
  setFormMsg(side, "", "");
}

async function doSaveProfile(side) {
  const f = paneOf(side);
  const backup = f._editBackup;
  const wasModal = $("#form-" + side).classList.contains("modal-mode");
  setFormMsg(side, "保存中…", "");
  setBusy(true);
  try {
    const saved = await persistProfile(side, formProfile(side));
    if (!saved) return;
    toast(backup && backup.hadConn ? "配置已保存，当前连接未变更" : "连接配置已保存");
    if (wasModal || (backup && backup.hadConn)) closeConnectionForm(side);
    else setFormMsg(side, "配置已保存，尚未连接数据库", "ok");
  } catch (e) {
    setFormMsg(side, "保存失败: " + e, "err");
  } finally {
    setBusy(false);
  }
}

async function doConnect(side) {
  setFormMsg(side, "正在保存配置…", "");
  setBusy(true);
  try {
    const saved = await persistProfile(side, formProfile(side));
    if (!saved) return;
    setFormMsg(side, "配置已保存，正在连接…", "");
    const r = await api("connect", side, saved, false);
    if (!r.ok) {
      setFormMsg(side, "配置已保存；" + (r.msg || "连接失败"), "err");
      return;
    }
    applyState(r);
    if (S.mode && S.sides.left && S.sides.right) await loadTableList();
    closeConnectionForm(side);
    toast((side === "left" ? "左侧" : "右侧") + "连接成功");
  } catch (e) {
    setFormMsg(side, "配置已保存；连接失败: " + e, "err");
  } finally {
    setBusy(false);
  }
}

async function doSwitch(side, pid) {
  if (pid === "__new__") {
    // 新建连接: 弹模态框, 不动当前连接
    fillForm(side, null);
    $("#form-" + side).classList.add("modal-mode");
    const btnCancel = $(".btn-cancel-edit", paneOf(side));
    if (btnCancel) btnCancel.style.display = "";
    paneOf(side)._editBackup = {
      hadConn: !!S.sides[side],
      profileId: S.sides[side] ? S.sides[side].profile_id : null,
    };
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

async function doDelete(side) {
  const info = S.sides[side];
  if (!info || !info.profile_id) { toast("当前没有已连接的数据源"); return; }
  const p = S.profiles.find(x => x.id === info.profile_id);
  deleteProfile(side, info.profile_id, p ? (p.name || p.id) : "该数据源");
}

async function deleteProfile(side, pid, name) {
  if (!confirm("确认删除数据源「" + name + "」?\n删除后不可恢复。")) return;
  setBusy(true);
  try {
    const r = await api("delete_profile", pid);
    if (!r.ok) { toast(r.msg || "删除失败"); return; }
    applyState(r);
    toast((side === "left" ? "左侧" : "右侧") + "数据源已删除");
  } finally { setBusy(false); }
}

async function doDisconnect(side) {
  const r = await api("disconnect", side);
  applyState(r);
  if (S.mode) { S.mode = null; }
  renderAll();
}

function applyState(r) {
  const oldLeft = S.sides.left && S.sides.left.profile_id;
  const oldRight = S.sides.right && S.sides.right.profile_id;
  S.profiles = r.profiles || [];
  S.sides.left = r.left;
  S.sides.right = r.right;
  const connectionsChanged = oldLeft !== (r.left && r.left.profile_id)
    || oldRight !== (r.right && r.right.profile_id);
  if (connectionsChanged) {
    ++S.tableListRequest;
    S.tables = { left: [], right: [] };
    closeTablePickers();
  }
  if (!(S.sides.left && S.sides.right)) S.mode = null;
  renderAll();
  if (connectionsChanged && S.mode && S.sides.left && S.sides.right) loadTableList();
}

function setBusy(b) {
  S.busy = b;
  document.body.classList.toggle("loading", b);
}

/* ---------------- 对比表(结构) ---------------- */

function parseTables(text) {
  return text.split(/[\s,，;；\n]+/).map(s => s.trim()).filter(Boolean);
}

function tableCatalog() {
  const left = new Set(S.tables.left);
  const right = new Set(S.tables.right);
  return Array.from(new Set([...left, ...right]))
    .sort((a, b) => a.localeCompare(b, "zh-CN", { sensitivity: "base" }))
    .map(name => ({ name, left: left.has(name), right: right.has(name) }));
}

function tableAvailability(table) {
  if (table.left && table.right) return { text: "两侧", cls: "both" };
  return table.left ? { text: "← 仅左", cls: "left" } : { text: "仅右 →", cls: "right" };
}

function closeTablePickers(except) {
  $$(".table-picker.open").forEach(picker => {
    if (picker === except) return;
    picker.classList.remove("open");
    $(".table-picker-toggle", picker).setAttribute("aria-expanded", "false");
  });
}

function syncTableInputs(selector, value) {
  const picker = selector === ".table-input" ? "#shared-struct-picker" : "#shared-data-picker";
  const field = $(selector, $(picker));
  if (field) field.value = value;
}

function renderTableMenu(picker) {
  const mode = picker.dataset.picker;
  const menu = $(".table-menu", picker);
  const selected = new Set(parseTables($(mode === "struct" ? ".table-input" : ".data-table-input", picker).value));
  const query = (picker.dataset.filter || "").trim().toLocaleLowerCase();
  const tables = tableCatalog().filter(t => !query || t.name.toLocaleLowerCase().includes(query));

  menu.innerHTML = "";
  if (mode === "struct") {
    const tools = document.createElement("div");
    tools.className = "table-menu-tools";
    tools.innerHTML = `<input class="table-menu-search" value="${esc(picker.dataset.filter || "")}" placeholder="搜索表名" autocomplete="off"><button type="button" class="table-menu-clear">清空</button>`;
    menu.appendChild(tools);
    const search = $(".table-menu-search", tools);
    search.addEventListener("input", e => {
      picker.dataset.filter = e.target.value;
      renderTableMenu(picker);
      const next = $(".table-menu-search", picker);
      next.focus();
      next.setSelectionRange(next.value.length, next.value.length);
    });
    $(".table-menu-clear", tools).addEventListener("click", () => {
      syncTableInputs(".table-input", "");
      renderTableMenu(picker);
    });
  }

  const options = document.createElement("div");
  options.className = "table-menu-options";
  if (!tables.length) {
    options.innerHTML = `<div class="table-menu-empty">${tableCatalog().length ? "没有匹配的表" : "暂无数据表，请刷新连接"}</div>`;
  }
  for (const table of tables) {
    const availability = tableAvailability(table);
    const unavailable = mode === "data" && !(table.left && table.right);
    const option = document.createElement("button");
    option.type = "button";
    option.className = "table-option" + (selected.has(table.name) ? " selected" : "") + (unavailable ? " unavailable" : "");
    option.dataset.table = table.name;
    option.disabled = unavailable;
    option.setAttribute("role", "option");
    option.setAttribute("aria-selected", selected.has(table.name) ? "true" : "false");
    option.innerHTML = `${mode === "struct" ? '<span class="table-check"></span>' : ""}<span class="table-option-name">${esc(table.name)}</span><span class="table-side ${availability.cls}">${availability.text}</span>`;
    option.addEventListener("click", () => {
      if (mode === "data") {
        syncTableInputs(".data-table-input", table.name);
        for (const p of $$('.table-picker[data-picker="data"]')) p.dataset.filter = "";
        closeTablePickers();
        return;
      }
      const current = parseTables($(".table-input", picker).value);
      const wasSelected = current.includes(table.name);
      const next = wasSelected
        ? current.filter(name => name !== table.name)
        : [...current, table.name];
      syncTableInputs(".table-input", next.join(", "));
      option.classList.toggle("selected", !wasSelected);
      option.setAttribute("aria-selected", wasSelected ? "false" : "true");
      const search = $(".table-menu-search", picker);
      if (search) search.focus({ preventScroll: true });
    });
    options.appendChild(option);
  }
  menu.appendChild(options);
}

function openTablePicker(picker, focusSearch) {
  closeTablePickers(picker);
  picker.classList.add("open");
  $(".table-picker-toggle", picker).setAttribute("aria-expanded", "true");
  renderTableMenu(picker);
  if (focusSearch) {
    const search = $(".table-menu-search", picker);
    if (search) search.focus();
  }
}

function bindTablePicker(mode) {
  const picker = $(mode === "struct" ? "#shared-struct-picker" : "#shared-data-picker");
  const field = $(mode === "struct" ? ".table-input" : ".data-table-input", picker);
  const toggle = $(".table-picker-toggle", picker);
  const menu = $(".table-menu", picker);
  const selector = mode === "struct" ? ".table-input" : ".data-table-input";

  menu.addEventListener("click", e => e.stopPropagation());
  toggle.addEventListener("click", e => {
    e.stopPropagation();
    if (picker.classList.contains("open")) {
      closeTablePickers();
    } else {
      picker.dataset.filter = "";
      openTablePicker(picker, mode === "struct");
    }
  });
  field.addEventListener("input", e => {
    syncTableInputs(selector, e.target.value);
    if (mode === "data") {
      picker.dataset.filter = e.target.value;
      openTablePicker(picker, false);
    } else if (picker.classList.contains("open")) {
      renderTableMenu(picker);
    }
  });
  if (mode === "data") {
    field.addEventListener("focus", () => {
      if (!picker.classList.contains("open")) {
        picker.dataset.filter = "";
        openTablePicker(picker, false);
      }
    });
  }
  field.addEventListener("keydown", e => {
    if (e.key === "Escape") {
      closeTablePickers();
      return;
    }
    if (e.key === "ArrowDown" && picker.classList.contains("open")) {
      const first = $(".table-option:not(:disabled)", picker);
      if (first) { e.preventDefault(); first.focus(); }
    }
  });
}

async function doCompareStruct() {
  const text = $(".table-input", $("#shared-struct-picker")).value;
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
  same: "一致", diff: "有差异", only_left: "← 仅左侧", only_right: "仅右侧 →", missing_both: "两侧均无",
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

/* ---------------- 对比数据 ---------------- */

async function doCompareData() {
  const t = $(".data-table-input", $("#shared-data-picker")).value.trim();
  if (!t) { toast("请输入表名"); return; }
  let where = $(".data-where-input", $("#shared-where")).value.trim();
  if (!where) {
    where = "";
  } else {
    // 兼容用户填了前缀 where 的情况
    const lw = where.toLowerCase();
    if (lw.startsWith("where ")) where = where.slice(6).trim();
    else if (lw.startsWith("where")) where = where.slice(5).trim();
  }
  setBusy(true);
  try {
    const preview = await api("preview_data_compare", t, where);
    if (!preview.ok) { toast(preview.msg || "无法统计筛选范围", 4000); return; }
    if (preview.requires_confirmation) {
      setBusy(false);
      const proceed = confirm(
        `筛选范围超过 ${preview.warning_threshold} 行：\n` +
        `左侧 ${preview.left_count} 行，右侧 ${preview.right_count} 行。\n\n` +
        "建议先填写 WHERE 缩小范围，避免等待时间过长。\n" +
        "点击“确定”仍然比对，点击“取消”返回填写 WHERE。"
      );
      if (!proceed) {
        $(".data-where-input", $("#shared-where")).focus();
        return;
      }
      setBusy(true);
    }
    const r = await api("compare_data", t, where);
    if (!r.ok) { toast(r.msg || "比对失败", 4000); return; }
    renderDataResults(r);
  } finally {
    setBusy(false);
  }
}

function kindTag(kind) {
  if (kind === "only_left") return '<span class="tag tag-only_left">← 仅左侧</span>';
  if (kind === "only_right") return '<span class="tag tag-only_right">仅右侧 →</span>';
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
      <span>← 仅左侧 <b>${r.only_left}</b></span>
      <span>仅右侧 → <b>${r.only_right}</b></span>
      <span>内容不同 <b>${r.updated}</b></span>
      ${r.no_pk ? '<span style="color:var(--err)">⚠ 无主键, 仅识别多/少行</span>' : ""}
    </div>
    ${r.detail_capped ? '<div style="color:var(--text-dim);font-size:11px;padding-bottom:6px">差异超过 2000 条，明细仅展示前 200 条，修复SQL见下方SQL区</div>' : ""}
    ${r.sql_capped ? '<div style="color:var(--err);font-size:12px;padding-bottom:6px">修复SQL超过单方向 5000 条，当前输出已截断，不能作为完整修复方案；请填写 WHERE 分批处理。</div>' : ""}
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
  const request = ++S.tableListRequest;
  const results = await Promise.all(SIDES.map(async side => {
    if (!S.sides[side]) return [];
    try {
      const r = await api("list_tables", side);
      return r.ok ? r.tables : [];
    } catch (_e) {
      return [];
    }
  }));
  if (request !== S.tableListRequest) return;
  S.tables.left = results[0];
  S.tables.right = results[1];
  $$(".table-picker.open").forEach(renderTableMenu);
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
  $(".btn-save", f).addEventListener("click", () => doSaveProfile(side));
  $(".btn-connect", f).addEventListener("click", () => doConnect(side));
  $("#disc-" + side).addEventListener("click", () => doDisconnect(side));
  $("#refresh-" + side).addEventListener("click", () => doRefresh(side));
  $("#edit-" + side).addEventListener("click", () => doEdit(side));
  $("#del-" + side).addEventListener("click", () => doDelete(side));
  const btnCancel = $(".btn-cancel-edit", paneOf(side));
  if (btnCancel) btnCancel.addEventListener("click", () => doCancelEdit(side));
  // 自定义下拉
  const dd = $("#dd-" + side);
  const ddDisplay = $("#dd-display-" + side);
  ddDisplay.addEventListener("click", () => {
    // 关闭其他侧的下拉
    for (const s of SIDES) {
      if (s !== side) $("#dd-" + s).classList.remove("open");
    }
    dd.classList.toggle("open");
  });
  // 下拉里的操作按钮
  $("#dd-list-" + side).addEventListener("click", e => {
    const editBtn = e.target.closest(".dd-edit");
    const deleteBtn = e.target.closest(".dd-delete");
    if (editBtn) {
      e.stopPropagation();
      dd.classList.remove("open");
      const pid = editBtn.dataset.pid;
      const p = S.profiles.find(x => x.id === pid);
      if (!p) return;
      // 如果该数据源已连接在当前侧, 先断开再编辑; 如果连在另一侧, 不动连接, 仅在当前侧填表单
      // 实际上: 点编辑就是想改配置, 不影响连接。直接回填表单 + 进入编辑态
      fillForm(side, p);
      $("#form-" + side).style.display = "";
      $("#ws-" + side).style.display = "none";
      // 记录编辑前状态: 从下拉进编辑, 之前可能已连或未连
      paneOf(side)._editBackup = { hadConn: !!(S.sides[side]), profileId: S.sides[side] ? S.sides[side].profile_id : null };
      const btnCancel = $(".btn-cancel-edit", paneOf(side));
      if (btnCancel) btnCancel.style.display = "";
      setFormMsg(side, "仅保存不会中断当前连接；保存并连接成功后才会替换当前连接", "");
      return;
    }
    if (deleteBtn) {
      e.stopPropagation();
      dd.classList.remove("open");
      const pid = deleteBtn.dataset.pid;
      const p = S.profiles.find(x => x.id === pid);
      if (!p) return;
      deleteProfile(side, pid, p.name || p.id);
    }
  });
  // 点其他地方关闭下拉
  document.addEventListener("click", e => {
    if (!dd.contains(e.target)) dd.classList.remove("open");
  });
  $(".btn-copy", f).addEventListener("click", () => copySQL(side));
  // SQL 输出区拉杆拖拽 (两侧同步高度)
  bindSqlResizer(side);
  onTypeChange(side);
}

function bindSqlResizer(side) {
  const resizer = $(".sql-resizer", $("#ws-" + side));
  if (!resizer) return;
  resizer.addEventListener("mousedown", e => {
    e.preventDefault();
    const area = resizer.parentElement;
    const startY = e.clientY;
    const startH = area.getBoundingClientRect().height;
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    function onMove(ev) {
      const dh = ev.clientY - startY;
      // 拉杆在 SQL 区顶部：向上拖应增高，向下拖应缩小。
      let h = Math.max(100, Math.min(800, startH - dh));
      area.style.height = h + "px";
      // 同步对侧
      const other = side === "left" ? "right" : "left";
      const dst = $(".sql-area", $("#ws-" + other));
      if (dst) dst.style.height = h + "px";
    }
    function onUp() {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

async function doRefresh(side) {
  // 重新拉取状态 + 刷新表列表
  setBusy(true);
  try {
    const r = await api("refresh", side);
    if (!r.ok) { toast(r.msg || "刷新失败"); return; }
    applyState(r);
    if (S.sides.left && S.sides.right) await loadTableList();
    toast((side === "left" ? "左侧" : "右侧") + "已刷新");
  } finally {
    setBusy(false);
  }
}

async function doEdit(side) {
  // 编辑模式: 弹模态框回填表单, 不动当前连接
  const info = S.sides[side];
  if (!info || !info.profile_id) { toast("当前没有已连接的数据源"); return; }
  const p = S.profiles.find(x => x.id === info.profile_id);
  if (!p) { toast("找不到该数据源的配置"); return; }
  paneOf(side)._editBackup = { hadConn: true, profileId: info.profile_id };
  fillForm(side, p);
  $("#form-" + side).classList.add("modal-mode");
  // 显示取消按钮
  const btnCancel = $(".btn-cancel-edit", paneOf(side));
  if (btnCancel) btnCancel.style.display = "";
  setFormMsg(side, "仅保存不会中断当前连接；保存并连接成功后才会替换当前连接", "");
}

function doCancelEdit(side) {
  closeConnectionForm(side);
}

function setMode(m) {
  if (!(S.sides.left && S.sides.right)) return;
  // 单选: 点击哪个就选哪个, 不再 toggle
  if (S.mode === m) return;
  closeTablePickers();
  // 切换模式时清空两侧的比对结果和 SQL 输出
  for (const side of SIDES) {
    const ws = $("#ws-" + side);
    const ra = $(".result-area", ws);
    const so = $(".sql-output", ws);
    if (ra) ra.innerHTML = "";
    if (so) so.value = "";
  }
  S.mode = m;
  renderModes();
  loadTableList();
}

async function init() {
  for (const side of SIDES) bindPane(side);
  bindTablePicker("struct");
  bindTablePicker("data");
  document.addEventListener("click", e => {
    if (!e.target.closest(".table-picker")) closeTablePickers();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") closeTablePickers();
  });
  $("#btnModeStruct").addEventListener("click", () => setMode("struct"));
  $("#btnModeData").addEventListener("click", () => setMode("data"));
  $("#btnCompareShared").addEventListener("click", () => S.mode === "data" ? doCompareData() : doCompareStruct());
  const st = await api("get_state");
  applyState(st);
  // 自动恢复上次连接 (两侧并行)
  const toRestore = [];
  if (st.last_left && !st.left) toRestore.push("left");
  if (st.last_right && !st.right) toRestore.push("right");
  if (toRestore.length) {
    setBusy(true);
    try {
      // 串行恢复, 避免同类型检查竞态
      for (const side of toRestore) {
        const r = await api("restore_connect", side);
        if (r.ok) applyState(r);
      }
    } finally {
      setBusy(false);
    }
  }
  // 默认选择「对比表」模式 (如果两侧都已连)
  if (S.sides.left && S.sides.right && !S.mode) {
    S.mode = "struct";
    renderModes();
    await loadTableList();
  }
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
    if (p.password) $(".f-pwd", f).value = p.password;
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
