(function () {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  let state = null;
  let initialized = false;

  function api() {
    if (!window.pywebview || !window.pywebview.api) throw new Error("MCP 配置窗口尚未准备好");
    return window.pywebview.api;
  }

  function notice(message, error) {
    const node = $("#notice");
    node.textContent = message || "";
    node.className = "notice" + (error ? " error" : "");
  }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>\"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }

  function render() {
    const project = state && state.project || "";
    const projectInput = $("#projectPath");
    if (document.activeElement !== projectInput) projectInput.value = project;
    $("#scanState").textContent = state ? "扫描完成" : "正在扫描…";
    $("#scanState").className = "scan-state" + (state ? " ok" : "");
    const grid = $("#agents");
    if (!state) { grid.className = "agents-grid empty-grid"; grid.innerHTML = '<div class="empty-state">选择项目后显示 Codex、Claude Code 和 OpenCode</div>'; return; }
    grid.className = "agents-grid";
    grid.innerHTML = state.agents.map((agent) => {
      const detected = agent.installed;
      const globalText = agent.global_installed ? "已全局安装" : "全局安装";
      const projectText = agent.project_installed ? "已项目安装" : "项目安装";
      const projectDisabled = !project || agent.global_installed || agent.project_installed || !detected;
      const globalDisabled = agent.global_installed || !detected;
      let projectState = "未安装";
      let projectClass = "";
      if (agent.global_installed) { projectState = "全局已安装，项目安装不可用"; projectClass = "installed"; }
      else if (agent.project_installed) { projectState = "当前项目已安装"; projectClass = "installed"; }
      else if (!project) { projectState = "选择项目后可安装"; }
      return `<article class="agent-card">
        <div class="agent-head"><div class="agent-name">${esc(agent.name)}</div><span class="agent-status ${detected ? "detected" : ""}">${detected ? "已检测" : "未检测到"}</span></div>
        <div class="agent-binary" title="${esc(agent.binary || "未找到命令")}">${esc(agent.binary || "未找到命令")}</div>
        <div class="install-row"><div><span class="install-label">所有项目</span><button class="button button-primary install-global" data-agent="${esc(agent.id)}" ${globalDisabled ? "disabled" : ""}>${globalText}</button></div>
          <div><span class="install-label">当前项目</span><button class="button button-secondary install-project" data-agent="${esc(agent.id)}" ${projectDisabled ? "disabled" : ""}>${projectText}</button></div></div>
        <div class="install-state ${projectClass}">${esc(projectState)}</div>
      </article>`;
    }).join("");
    grid.querySelectorAll("button.install-global").forEach((button) => button.addEventListener("click", () => install(button.dataset.agent, "global")));
    grid.querySelectorAll("button.install-project").forEach((button) => button.addEventListener("click", () => install(button.dataset.agent, "project")));
  }

  async function scan() {
    if (!$("#projectPath").value.trim()) { state = null; $("#refresh").hidden = true; render(); return; }
    $("#refresh").hidden = false;
    $("#scanState").textContent = "正在检查…";
    try { state = await api().scan($("#projectPath").value); render(); notice(""); }
    catch (error) { notice(String(error), true); }
  }

  async function install(agent, scope) {
    const scopeText = scope === "global" ? "所有项目" : "当前项目";
    if (!window.confirm(`确认安装到${scopeText}？`)) return;
    try {
      const result = await api().install(agent, scope, $("#projectPath").value);
      notice(result.msg || (result.ok ? "安装完成" : "安装失败"), !result.ok);
      if (result.state) { state = result.state; render(); }
      else await scan();
    } catch (error) { notice(String(error), true); }
  }

  async function chooseProject() {
    try {
      const result = await api().choose_project();
      if (result.ok && result.path) { $("#projectPath").value = result.path; await scan(); }
      else if (!result.ok) notice(result.msg, true);
    } catch (error) { notice(String(error), true); }
  }

  function init() {
    if (initialized) return;
    initialized = true;
    $("#refresh").addEventListener("click", scan);
    $("#chooseProject").addEventListener("click", chooseProject);
    $("#clearProject").addEventListener("click", async () => { $("#projectPath").value = ""; await scan(); });
    $("#projectPath").addEventListener("change", scan);
    render();
  }

  window.addEventListener("pywebviewready", init);
  if (window.pywebview && window.pywebview.api) init();
})();
