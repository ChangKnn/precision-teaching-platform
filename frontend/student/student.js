"use strict";

const state = {
  token: sessionStorage.getItem("student_token") || "",
  student: null,
  loginOptions: [],
  tasks: [],
  activeTask: null,
  session: null,
  typing: false,
  timerId: null,
  reportPollId: null
};

const $ = (selector, parent = document) => parent.querySelector(selector);
const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
const loginView = $("#loginView");
const studentApp = $("#studentApp");
const loginForm = $("#loginForm");
const messageForm = $("#messageForm");
const messageInput = $("#messageInput");
const messages = $("#messages");
const subjectSelect = $("#subjectSelect");
const submitDialog = $("#submitDialog");

function escapeHtml(value) {
  const span = document.createElement("span");
  span.textContent = value ?? "";
  return span.innerHTML;
}

function renderReportInline(value) {
  return escapeHtml(value).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
}

function renderReportMarkdown(markdown) {
  const html = [];
  let paragraph = [];
  let list = "";
  let code = [];
  let inCode = false;
  const closeParagraph = () => {
    if (paragraph.length) html.push(`<p>${paragraph.map(renderReportInline).join("<br>")}</p>`);
    paragraph = [];
  };
  const closeList = () => {
    if (list) html.push(`</${list}>`);
    list = "";
  };
  for (const line of String(markdown || "").replaceAll("\r\n", "\n").split("\n")) {
    if (/^\s*```/.test(line)) {
      closeParagraph(); closeList();
      if (inCode) { html.push(`<pre>${escapeHtml(code.join("\n"))}</pre>`); code = []; }
      inCode = !inCode;
      continue;
    }
    if (inCode) { code.push(line); continue; }
    const trimmed = line.trim();
    if (!trimmed) { closeParagraph(); closeList(); continue; }
    const heading = trimmed.match(/^(#{1,5})\s+(.+)$/);
    if (heading) {
      closeParagraph(); closeList();
      html.push(`<h${Math.min(5, heading[1].length + 2)}>${renderReportInline(heading[2])}</h${Math.min(5, heading[1].length + 2)}>`);
      continue;
    }
    if (/^---+$/.test(trimmed)) { closeParagraph(); closeList(); html.push("<hr>"); continue; }
    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    const numbered = trimmed.match(/^\d+[.、]\s+(.+)$/);
    if (bullet || numbered) {
      closeParagraph();
      const type = bullet ? "ul" : "ol";
      if (list !== type) { closeList(); html.push(`<${type}>`); list = type; }
      html.push(`<li>${renderReportInline((bullet || numbered)[1])}</li>`);
      continue;
    }
    closeList();
    if (trimmed.startsWith("> ")) { closeParagraph(); html.push(`<blockquote>${renderReportInline(trimmed.slice(2))}</blockquote>`); }
    else paragraph.push(trimmed);
  }
  closeParagraph(); closeList();
  if (code.length) html.push(`<pre>${escapeHtml(code.join("\n"))}</pre>`);
  return html.join("");
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers["X-Student-Token"] = state.token;
  if (options.body && !(options.body instanceof Blob) && typeof options.body === "string") {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const data = await response.json();
      message = data.detail || message;
    } catch (_) {
      // Use the status message for non-JSON failures.
    }
    if (response.status === 401) clearLogin();
    throw new Error(message);
  }
  return response.json();
}

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(date);
}

function setBusy(button, busy, busyLabel, normalLabel) {
  button.disabled = busy;
  button.textContent = busy ? busyLabel : normalLabel;
}

function renderLoginOptions(resetClass = false) {
  const schoolSelect = $("#schoolInput");
  const classSelect = $("#classInput");
  const previousSchool = schoolSelect.value;
  const previousClass = resetClass ? "" : classSelect.value;
  schoolSelect.replaceChildren(new Option(state.loginOptions.length ? "请选择学校" : "暂无已发布任务", ""));
  state.loginOptions.forEach(school => schoolSelect.append(new Option(school.name, school.name)));
  schoolSelect.value = state.loginOptions.some(school => school.name === previousSchool) ? previousSchool : "";
  const selectedSchool = state.loginOptions.find(school => school.name === schoolSelect.value);
  const classes = selectedSchool?.classes || [];
  classSelect.replaceChildren(new Option(selectedSchool ? "请选择班级" : "请先选择学校", ""));
  classes.forEach(item => classSelect.append(new Option(item.name, item.name)));
  classSelect.value = classes.some(item => item.name === previousClass) ? previousClass : "";
  classSelect.disabled = !selectedSchool;
  $(".login-button", loginForm).disabled = !state.loginOptions.length;
  const availability = $("#loginAvailability");
  availability.hidden = Boolean(state.loginOptions.length);
  availability.textContent = "暂无已发布的诊断任务，请联系老师。";
}

async function loadLoginOptions() {
  try {
    const response = await fetch("/api/student/login-options");
    if (!response.ok) throw new Error("无法读取学校与班级");
    const payload = await response.json();
    state.loginOptions = payload.schools || [];
    renderLoginOptions();
  } catch (_) {
    $("#loginAvailability").textContent = "学校与班级加载失败，请刷新页面重试。";
    $("#loginAvailability").hidden = false;
    $(".login-button", loginForm).disabled = true;
  }
}

$("#schoolInput").addEventListener("change", () => renderLoginOptions(true));

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const loginButton = $(".login-button", loginForm);
  const payload = {
    school_name: $("#schoolInput").value.trim(),
    class_name: $("#classInput").value.trim(),
    name: $("#nameInput").value.trim()
  };
  if (!Object.values(payload).every(Boolean)) {
    $("#loginError").textContent = "请选择学校、班级并填写姓名。";
    $("#loginError").hidden = false;
    return;
  }
  const selectedSchool = state.loginOptions.find(school => school.name === payload.school_name);
  if (!selectedSchool?.classes.some(item => item.name === payload.class_name)) {
    $("#loginError").textContent = "请选择老师已发布任务的学校和班级。";
    $("#loginError").hidden = false;
    return;
  }
  $("#loginError").hidden = true;
  setBusy(loginButton, true, "正在进入…", "进入学习空间");
  try {
    const result = await api("/api/student/login", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    state.token = result.token;
    sessionStorage.setItem("student_token", state.token);
    await enterApp(result);
  } catch (error) {
    $("#loginError").textContent = error.message;
    $("#loginError").hidden = false;
  } finally {
    loginButton.disabled = false;
    loginButton.innerHTML = '进入学习空间<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg>';
  }
});

async function enterApp(payload) {
  state.student = payload.student;
  state.tasks = payload.tasks || [];
  loginView.hidden = true;
  studentApp.hidden = false;
  $("#studentNameLabel").textContent = state.student.name;
  $("#studentClassLabel").textContent = state.student.class_name;
  $("#studentAvatar").textContent = state.student.name.slice(-1);
  populateSubjects();
  renderHistory();
  if (state.tasks.length) {
    const preferred = state.tasks.find((task) => task.session_status === "in_progress") || state.tasks[0];
    subjectSelect.value = preferred.subject;
    await openTask(preferred.teaching_id);
  } else {
    renderNoTask();
  }
}

function populateSubjects() {
  const subjects = [...new Set(state.tasks.map((task) => task.subject))];
  subjectSelect.replaceChildren();
  if (!subjects.length) {
    subjectSelect.append(new Option("暂无任务", ""));
    subjectSelect.disabled = true;
    return;
  }
  subjectSelect.disabled = false;
  subjects.forEach((subject) => subjectSelect.append(new Option(subject, subject)));
}

function renderHistory() {
  const list = $("#historyList");
  $("#historyCount").textContent = `${state.tasks.length} 次`;
  if (!state.tasks.length) {
    list.innerHTML = '<p class="history-empty">老师尚未向本班发布诊断任务</p>';
    return;
  }
  list.innerHTML = state.tasks.map((task) => {
    const active = state.activeTask?.teaching_id === task.teaching_id;
    const status = task.report_status === "pushed" ? "已收到反馈" : task.session_status === "submitted" ? "已提交" : task.session_status === "in_progress" ? "进行中" : "待开始";
    const time = task.submitted_at || task.started_at || task.updated_at;
    return `<button class="history-item${active ? " active" : ""}" data-teaching-id="${escapeHtml(task.teaching_id)}">
      <span class="history-status ${task.session_status === "submitted" ? "done" : "live"}"></span>
      <span><strong>${escapeHtml(task.title)}</strong><small>${status} · ${escapeHtml(formatTime(time))}</small></span>
    </button>`;
  }).join("");
  $$('[data-teaching-id]', list).forEach((button) => button.addEventListener("click", async () => {
    try {
      await openTask(button.dataset.teachingId);
      subjectSelect.value = state.activeTask.subject;
      closeSidebar();
    } catch (error) {
      showToast("任务加载失败", error.message);
    }
  }));
}

subjectSelect.addEventListener("change", async () => {
  const task = state.tasks.find((item) => item.subject === subjectSelect.value);
  if (!task) return;
  try {
    await openTask(task.teaching_id);
    closeSidebar();
  } catch (error) {
    showToast("任务加载失败", error.message);
  }
});

async function openTask(teachingId) {
  state.typing = false;
  const result = await api(`/api/student/tasks/${encodeURIComponent(teachingId)}/session`, { method: "POST" });
  state.activeTask = { ...state.tasks.find((task) => task.teaching_id === teachingId), ...result.task };
  state.session = result.session;
  state.tasks = state.tasks.map((task) => task.teaching_id === teachingId
    ? {
        ...task,
        session_id: state.session.id,
        session_status: state.session.status,
        started_at: state.session.started_at,
        report_status: state.session.report?.status || task.report_status,
        report_pushed_at: state.session.report?.pushed_at || task.report_pushed_at
      }
    : task);
  renderTask();
  renderHistory();
  renderMessages();
  updateSubmissionSummary();
  startTimer();
  startReportPolling();
}

function renderTask() {
  const task = state.activeTask;
  $("#taskCard").hidden = false;
  $("#subjectDot").textContent = task.subject;
  $("#taskTitleHeader").textContent = task.title;
  $("#teacherLabel").textContent = `${task.teacher_name}发布`;
  $("#taskCardTitle").textContent = task.title;
  $("#taskDescription").textContent = task.task_text;
  $("#durationText").textContent = task.duration_minutes;
  $("#taskRequirements").innerHTML = "<li>与 AI 对话，完整说明你的想法和理由</li><li>最终作答文本可选，留空也能提交</li>";
  const submitted = state.session.status === "submitted";
  const reportReady = state.session.report?.status === "pushed";
  $("#submitButtonLabel").textContent = submitted ? "已提交" : "提交给老师";
  $$("[data-open-submit]").forEach((button) => button.disabled = submitted);
  messageInput.disabled = submitted && !reportReady;
  messageInput.placeholder = reportReady
    ? "结合老师的反馈，说说你现在的理解……"
    : submitted ? "等待老师推送反馈报告" : "说说你现在的想法……";
  resizeComposer();
}

function renderNoTask() {
  $("#taskCard").hidden = true;
  messages.innerHTML = '<article class="task-card"><h1>暂无学习任务</h1><p>老师发布并分配诊断任务后，会显示在这里。</p></article>';
  $("#taskTitleHeader").textContent = "暂无已发布任务";
  $("#teacherLabel").textContent = "请稍后再来查看";
  $("#subjectDot").textContent = "—";
  messageInput.disabled = true;
  $$("[data-open-submit]").forEach((button) => button.disabled = true);
}

function renderMessages() {
  const items = state.session?.messages || [];
  const report = state.session?.report;
  const reportTime = report?.pushed_at ? new Date(report.pushed_at).getTime() : Number.POSITIVE_INFINITY;
  let reportInserted = false;
  const fragments = [];
  const reportCard = () => `<article class="feedback-notice" role="status">
    <span class="feedback-notice-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 3h9l3 3v15H6z"/><path d="M9 11h6M9 15h6M15 3v4h4"/></svg></span>
    <span class="feedback-notice-copy"><small>教师反馈已送达</small><strong>你收到了本次诊断反馈报告</strong><span>可以查看报告，并结合原对话与最终成果继续复盘。</span></span>
    <button class="secondary-button" type="button" data-open-report>查看报告</button>
  </article>`;
  items.forEach((message) => {
    if (report && !reportInserted && new Date(message.created_at).getTime() > reportTime) {
      fragments.push(reportCard());
      reportInserted = true;
    }
    const isStudent = message.role === "student";
    fragments.push(`<article class="message ${isStudent ? "user" : "ai"}">
      <span class="message-avatar">${isStudent ? escapeHtml(state.student.name.slice(-1)) : "AI"}</span>
      <div class="message-body">
        <div class="message-meta"><span>${isStudent ? "我" : "思阶 AI"}</span><span>·</span><time>${escapeHtml(formatTime(message.created_at))}</time></div>
        <div class="message-bubble">${escapeHtml(message.content)}</div>
      </div>
    </article>`);
  });
  if (report && !reportInserted) fragments.push(reportCard());
  messages.innerHTML = fragments.join("");
  if (state.typing) {
    messages.insertAdjacentHTML("beforeend", '<article class="message ai"><span class="message-avatar">AI</span><div class="message-body"><div class="message-meta"><span>思阶 AI</span><span>·</span><time>正在回应</time></div><span class="typing"><i></i><i></i><i></i></span></div></article>');
  }
  requestAnimationFrame(() => {
    const scroller = $("#conversationScroll");
    scroller.scrollTop = scroller.scrollHeight;
  });
}

function openReport() {
  const report = state.session?.report;
  if (!report) return;
  $("#reportTaskTitle").textContent = state.activeTask?.title || "本次诊断任务";
  $("#reportPushedAt").textContent = `教师于 ${new Date(report.pushed_at).toLocaleString("zh-CN")} 推送`;
  $("#studentReportText").innerHTML = renderReportMarkdown(report.report_text);
  $("#reportDialog").showModal();
}

messages.addEventListener("click", (event) => {
  if (event.target.closest("[data-open-report]")) openReport();
});

$$('[data-close-report]').forEach((button) => button.addEventListener("click", () => $("#reportDialog").close()));
$("#reportDialog").addEventListener("click", (event) => {
  if (event.target === $("#reportDialog")) $("#reportDialog").close();
});

function stopReportPolling() {
  window.clearInterval(state.reportPollId);
  state.reportPollId = null;
}

function startReportPolling() {
  stopReportPolling();
  if (!state.session || state.session.status !== "submitted" || state.session.report) return;
  state.reportPollId = window.setInterval(async () => {
    try {
      const latest = await api(`/api/student/sessions/${encodeURIComponent(state.session.id)}`);
      if (!latest.report) return;
      state.session = latest;
      state.tasks = state.tasks.map((task) => task.teaching_id === state.activeTask.teaching_id
        ? { ...task, report_status: "pushed", report_pushed_at: latest.report.pushed_at }
        : task);
      stopReportPolling();
      renderTask();
      renderHistory();
      renderMessages();
      showToast("收到教师反馈", "点击对话中的提示卡片可以查看报告");
    } catch (_) {
      // 网络短暂波动时继续轮询，不打断学生当前页面。
    }
  }, 8000);
}

function resizeComposer() {
  messageInput.style.height = "auto";
  messageInput.style.height = `${Math.min(messageInput.scrollHeight, 130)}px`;
  $(".send-button", messageForm).disabled = messageInput.disabled || !messageInput.value.trim() || state.typing;
}

messageInput.addEventListener("input", resizeComposer);
messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    if (messageInput.value.trim()) messageForm.requestSubmit();
  }
});

messageForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = messageInput.value.trim();
  if (!content || !state.session || state.typing) return;
  const temporary = { id: `tmp-${Date.now()}`, role: "student", content, created_at: new Date().toISOString() };
  state.session.messages.push(temporary);
  messageInput.value = "";
  state.typing = true;
  resizeComposer();
  renderMessages();
  try {
    const result = await api(`/api/student/sessions/${encodeURIComponent(state.session.id)}/messages`, {
      method: "POST",
      body: JSON.stringify({ content })
    });
    state.session.messages = state.session.messages.filter((item) => item.id !== temporary.id);
    state.session.messages.push(result.student_message, result.assistant_message);
  } catch (error) {
    state.session.messages = state.session.messages.filter((item) => item.id !== temporary.id);
    messageInput.value = content;
    showToast("消息发送失败", error.message);
  } finally {
    state.typing = false;
    resizeComposer();
    renderMessages();
    updateSubmissionSummary();
  }
});

function startTimer() {
  window.clearInterval(state.timerId);
  const started = new Date(state.session.started_at).getTime();
  const paint = () => {
    const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
    const mins = String(Math.floor(seconds / 60)).padStart(2, "0");
    const secs = String(seconds % 60).padStart(2, "0");
    $("#elapsedTime").textContent = `已用 ${mins}:${secs}`;
  };
  paint();
  state.timerId = window.setInterval(paint, 1000);
}

function openSubmit() {
  if (!state.session || state.session.status === "submitted") return;
  $("#submittedText").value = state.session.submitted_text || "";
  updateSubmissionSummary();
  $("#submitError").hidden = true;
  submitDialog.showModal();
}

$$('[data-open-submit]').forEach((button) => button.addEventListener("click", openSubmit));

function updateSubmissionSummary() {
  const studentMessages = (state.session?.messages || []).filter((message) => message.role === "student").length;
  const submittedText = $("#submittedText").value.trim();
  const hasText = submittedText.length > 0;
  const textStatus = $("#textSubmissionStatus");
  $("#messageCountLabel").textContent = `${studentMessages} 条学生发言`;
  $("#submittedTextCount").textContent = $("#submittedText").value.length;
  $("#textStatusLabel").textContent = hasText ? `${submittedText.length} 字` : "未填写也可提交";
  textStatus.classList.toggle("complete", hasText);
  textStatus.classList.toggle("optional", !hasText);
  $("#textStatusIcon").innerHTML = hasText
    ? '<path d="m5 13 4 4L19 7"/>'
    : '<path d="M5 12h14"/>';
}

$("#submittedText").addEventListener("input", updateSubmissionSummary);

$("#confirmSubmitButton").addEventListener("click", async () => {
  const submittedText = $("#submittedText").value.trim();
  const button = $("#confirmSubmitButton");
  setBusy(button, true, "正在提交…", "确认提交给老师");
  try {
    state.session = await api(`/api/student/sessions/${encodeURIComponent(state.session.id)}/submit`, {
      method: "POST",
      body: JSON.stringify({ submitted_text: submittedText })
    });
    state.tasks = state.tasks.map((task) => task.teaching_id === state.activeTask.teaching_id
      ? { ...task, session_status: "submitted", submitted_at: state.session.submitted_at }
      : task);
    renderTask();
    renderHistory();
    startReportPolling();
    submitDialog.close();
    showToast("提交成功", submittedText ? "老师已收到你的对话记录和文字成果" : "老师已收到你的对话记录");
  } catch (error) {
    $("#submitError").textContent = error.message;
    $("#submitError").hidden = false;
  } finally {
    setBusy(button, false, "正在提交…", "确认提交给老师");
  }
});

submitDialog.addEventListener("click", (event) => {
  if (event.target === submitDialog) submitDialog.close();
});

function openSidebar() { $("#appSidebar").classList.add("open"); }
function closeSidebar() { $("#appSidebar").classList.remove("open"); }
$("#openSidebar").addEventListener("click", openSidebar);
$("#closeSidebar").addEventListener("click", closeSidebar);

function showToast(title, detail) {
  const toast = $("#toast");
  $("strong", toast).textContent = title;
  $("small", toast).textContent = detail;
  toast.hidden = false;
  window.setTimeout(() => { toast.hidden = true; }, 3400);
}

function clearLogin() {
  window.clearInterval(state.timerId);
  stopReportPolling();
  sessionStorage.removeItem("student_token");
  state.token = "";
  state.student = null;
  state.tasks = [];
  state.activeTask = null;
  state.session = null;
  studentApp.hidden = true;
  loginView.hidden = false;
  closeSidebar();
}

$("#logoutButton").addEventListener("click", async () => {
  try {
    await api("/api/student/session", { method: "DELETE" });
  } catch (_) {
    // Clear local state even if the server session already expired.
  }
  clearLogin();
  loginForm.reset();
  renderLoginOptions(true);
  $("#schoolInput").focus();
});

(async function restoreLogin() {
  loadLoginOptions();
  if (!state.token) return;
  try {
    const payload = await api("/api/student/bootstrap");
    await enterApp(payload);
  } catch (_) {
    clearLogin();
  }
})();
