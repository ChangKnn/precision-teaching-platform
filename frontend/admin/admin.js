"use strict";
const el = id => document.getElementById(id);
const state = {teachers: [], workspaces: [], offset: 0, total: 0, resetId: null};
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const date = value => value ? new Date(value).toLocaleString("zh-CN", {hour12:false}) : "尚未登录";
const labels = {draft:"草稿",active:"进行中",completed:"已完成",published:"已发布",pending:"待处理",confirmed:"已确认",diagnosis:"诊断设计",feedback:"结果反馈",intervention:"干预设计"};
const label = value => labels[value] || value || "—";
let noticeTimer;
function notice(message, error = false) {
  clearTimeout(noticeTimer);
  el("notice").textContent = message;
  el("notice").classList.toggle("error", error);
  el("notice").hidden = false;
  noticeTimer = setTimeout(() => { el("notice").hidden = true; }, error ? 10000 : 5000);
}
function showLogin() {
  el("dashboard").hidden = true; el("authView").hidden = false;
  el("adminLogin").hidden = false; el("initialPasswordForm").hidden = true;
}
async function api(path, method = "GET", payload) {
  const response = await fetch(path, {method, credentials:"same-origin", headers:{"Content-Type":"application/json"}, ...(payload === undefined ? {} : {body:JSON.stringify(payload)})});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && !path.endsWith("/login")) { document.querySelectorAll("dialog[open]").forEach(d => d.close()); showLogin(); }
    const detail = typeof data.detail === "string" ? data.detail : "提交的信息有误，请检查账号、密码长度和必填项";
    throw new Error(detail);
  }
  return data;
}
async function busy(form, action) {
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
  try { await action(); } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
}
async function initialize() {
  try {
    const status = await api("/api/admin-auth/status");
    if (!status.authenticated) { showLogin(); return; }
    el("adminLogin").reset();
    if (status.account.must_change_password) {
      el("authView").hidden = false; el("dashboard").hidden = true;
      el("adminLogin").hidden = true; el("initialPasswordForm").hidden = false;
      return;
    }
    el("authView").hidden = true; el("dashboard").hidden = false;
    el("signedInName").textContent = status.account.username;
    await refresh();
  } catch (error) { showLogin(); notice(error.message, true); }
}
async function refresh() {
  const [overview, teachers] = await Promise.all([api("/api/admin/overview"), api("/api/admin/teachers")]);
  for (const [id,key] of [["countTeachers","teachers"],["countEnabled","enabled_teachers"],["countTeachings","teachings"],["countSubmitted","submitted_sessions"]]) el(id).textContent = overview[key];
  state.teachers = teachers.teachers;
  state.workspaces = teachers.unbound_workspaces;
  el("teacherRows").innerHTML = state.teachers.map(t => `<tr><td><strong>${escapeHtml(t.teacher_name)}</strong><small>${escapeHtml(t.username)}</small></td><td>${escapeHtml(t.school_name)}<small>${escapeHtml(t.subject)}</small></td><td><span class="badge ${t.enabled ? "" : "disabled"}">${t.enabled ? "已启用" : "已停用"}</span>${t.must_change_password ? "<small>待首次改密</small>" : ""}</td><td>${t.teaching_count}</td><td>${escapeHtml(date(t.last_login_at))}</td><td><div class="row-actions"><button data-action="toggle" data-id="${escapeHtml(t.id)}">${t.enabled ? "停用" : "启用"}</button><button data-action="reset" data-id="${escapeHtml(t.id)}">重置密码</button><button data-action="tasks" data-id="${escapeHtml(t.id)}">查看任务</button></div></td></tr>`).join("");
  el("teacherEmpty").hidden = state.teachers.length > 0;
  const previousWorkspace = el("workspaceChoice").value;
  el("workspaceChoice").replaceChildren(new Option("新建教学空间", ""), ...state.workspaces.map(w => new Option(`${w.teacher_name} · ${w.school_name} · ${w.subject}（${w.teaching_count}项任务）`,w.id)));
  if (state.workspaces.some(w => w.id === previousWorkspace)) el("workspaceChoice").value = previousWorkspace;
  syncWorkspaceFields();
  const previousFilter = el("taskTeacher").value;
  const spaces = [...state.teachers.map(t => ({...t,id:t.workspace_id})), ...state.workspaces];
  el("taskTeacher").replaceChildren(new Option("全部教师空间", ""), ...spaces.map(w => new Option(`${w.teacher_name} · ${w.school_name}`, w.id)));
  if (spaces.some(w => w.id === previousFilter)) el("taskTeacher").value = previousFilter;
  await loadTasks();
}
function syncWorkspaceFields() {
  const workspace = state.workspaces.find(w => w.id === el("workspaceChoice").value);
  for (const [id,key] of [["createName","teacher_name"],["createSchool","school_name"],["createSubject","subject"]]) {
    el(id).disabled = !!workspace;
    if (workspace) el(id).value = workspace[key];
  }
}
function showPanel(name) {
  el("accountsPanel").hidden = name !== "accounts";
  el("teachingsPanel").hidden = name !== "teachings";
  el("accountsTab").setAttribute("aria-pressed", String(name === "accounts"));
  el("teachingsTab").setAttribute("aria-pressed", String(name === "teachings"));
}
async function loadTasks() {
  const query = new URLSearchParams({limit:"50", offset:String(state.offset), q:el("taskSearch").value.trim()});
  if (el("taskTeacher").value) query.set("workspace_id", el("taskTeacher").value);
  const data = await api(`/api/admin/teachings?${query}`);
  state.total = data.total;
  el("taskTotal").textContent = `${data.total} 项任务`;
  el("taskRows").innerHTML = data.teachings.map(t => `<tr><td><strong>${escapeHtml(t.title)}</strong><small>${escapeHtml(t.grade)} · ${escapeHtml(t.subject)} · ${escapeHtml(label(t.status))}</small></td><td>${escapeHtml(t.teacher_name || "历史空间")}<small>${escapeHtml(t.school_name)}${t.username ? "" : " · 未绑定账号"}</small></td><td>${escapeHtml(label(t.current_stage))}</td><td>${[t.diagnosis_status,t.feedback_status,t.intervention_status].map(v=>escapeHtml(label(v))).join(" / ")}</td><td>${t.submitted_count} / ${t.session_count}</td><td>${escapeHtml(date(t.updated_at))}</td><td><button data-task="${escapeHtml(t.id)}">只读详情</button></td></tr>`).join("");
  el("taskEmpty").hidden = data.teachings.length > 0;
  el("previousTasks").disabled = state.offset === 0;
  el("nextTasks").disabled = state.offset + 50 >= state.total;
  el("taskPage").textContent = data.total ? `${state.offset + 1}—${Math.min(state.offset + 50,data.total)} / ${data.total}` : "0 项";
}
async function changePassword(form, currentId, newId, repeatId) {
  if (el(newId).value !== el(repeatId).value) throw new Error("两次输入的新密码不一致");
  await api("/api/teacher-auth/password", "POST", {current_password:el(currentId).value,new_password:el(newId).value});
  form.reset();
  notice("密码已更新，其他设备的旧登录已失效");
}
el("adminLogin").addEventListener("submit", e => { e.preventDefault(); busy(e.target, async () => {
  await api("/api/admin-auth/login", "POST", {username:el("adminUsername").value.trim(), password:el("adminPassword").value});
  el("adminPassword").value = ""; await initialize();
}); });
el("initialPasswordForm").addEventListener("submit", e => { e.preventDefault(); busy(e.target, async () => { await changePassword(e.target,"initialCurrent","initialNew","initialRepeat"); await initialize(); }); });
el("createTeacher").addEventListener("submit", e => { e.preventDefault(); busy(e.target, async () => {
  await api("/api/admin/teachers", "POST", {username:el("createUsername").value.trim(),password:el("createPassword").value, school_name:el("createSchool").value.trim(), subject:el("createSubject").value.trim(), teacher_name:el("createName").value.trim(), workspace_id:el("workspaceChoice").value || null});
  e.target.reset(); syncWorkspaceFields(); await refresh(); notice("教师账号已创建，请单独告知教师临时密码");
}); });
el("workspaceChoice").addEventListener("change", syncWorkspaceFields);
el("accountsTab").addEventListener("click", () => showPanel("accounts"));
el("teachingsTab").addEventListener("click", () => showPanel("teachings"));
el("refreshButton").addEventListener("click", () => refresh().catch(e=>notice(e.message,true)));
el("filterTasks").addEventListener("submit", e => {e.preventDefault(); state.offset=0; loadTasks().catch(e=>notice(e.message,true));});
for (const [id,direction] of [["previousTasks",-1],["nextTasks",1]]) el(id).addEventListener("click", () => {state.offset=Math.max(0,state.offset+direction*50); loadTasks().catch(e=>notice(e.message,true));});
el("teacherRows").addEventListener("click", async e => {
  const button = e.target.closest("button[data-action]"); if (!button) return;
  const teacher = state.teachers.find(t=>t.id===button.dataset.id); if (!teacher) return;
  try {
    if (button.dataset.action === "toggle") {
      if (!confirm(`${teacher.enabled ? "停用" : "启用"}账号 ${teacher.username}？${teacher.enabled ? "该教师将立即退出，但资料和任务保留。" : ""}`)) return;
      button.disabled=true;
      await api(`/api/admin/teachers/${encodeURIComponent(teacher.id)}`, "PATCH", {enabled:!teacher.enabled}); await refresh(); notice("账号状态已更新");
    } else if (button.dataset.action === "reset") {
      state.resetId=teacher.id; el("resetForm").reset(); el("resetIdentity").textContent=`${teacher.teacher_name} · ${teacher.username}`; el("resetDialog").showModal();
    } else {
      el("taskTeacher").value=teacher.workspace_id; el("taskSearch").value=""; state.offset=0; showPanel("teachings"); await loadTasks();
    }
  } catch (error) {notice(error.message,true);} finally {button.disabled=false;}
});
el("resetForm").addEventListener("submit", e => {e.preventDefault(); busy(e.target, async () => {
  await api(`/api/admin/teachers/${encodeURIComponent(state.resetId)}/password`, "POST", {password:el("resetPassword").value}); e.target.reset(); el("resetDialog").close(); await refresh(); notice("密码已重置，教师需使用临时密码重新登录");
});});
el("ownPasswordButton").addEventListener("click", () => {el("ownPasswordForm").reset();el("ownPasswordDialog").showModal();});
el("ownPasswordForm").addEventListener("submit", e => {e.preventDefault();busy(e.target,async()=>{await changePassword(e.target,"ownCurrent","ownNew","ownRepeat");el("ownPasswordDialog").close();});});
document.querySelectorAll("[data-close-dialog]").forEach(button=>button.addEventListener("click",()=>button.closest("dialog").close()));
document.querySelectorAll("dialog").forEach(dialog=>dialog.addEventListener("close",()=>dialog.querySelector("form")?.reset()));
document.querySelectorAll("[data-logout]").forEach(button=>button.addEventListener("click",async()=>{try{await api("/api/teacher-auth/logout","POST");}finally{location.reload();}}));
el("taskRows").addEventListener("click", async e => {
  const button=e.target.closest("button[data-task]");if(!button)return;
  button.disabled=true;
  try {
    const data=await api(`/api/admin/teachings/${encodeURIComponent(button.dataset.task)}`);
    const t=data.teaching,d=data.diagnosis;
    const fields=[["精准教学主题",t.title],["教师",`${t.teacher_name || "—"} · ${t.school_name || "—"}`],["学科与年级",`${t.subject} · ${t.grade}`],["教材",t.textbook],["目标",t.goal],["教学内容",t.content],["设计构想",t.rationale],["当前阶段",label(t.current_stage)],["诊断任务状态",d ? label(d.status) : "尚未设计"],["诊断目标",d?.goal],["诊断任务内容",d?.task_text],["AI 对话角色",d?.ai_role],["任务班级",data.classrooms.map(c=>`${c.name}${c.is_published ? "（已发布）" : "（未发布）"}`).join("、")]];
    el("taskDetail").innerHTML=fields.map(([title,content])=>`<section class="detail-block"><h3>${escapeHtml(title)}</h3><p>${escapeHtml(content || "—")}</p></section>`).join("");
    el("taskDialog").showModal();
  } catch(error){notice(error.message,true);} finally{button.disabled=false;}
});
initialize();
