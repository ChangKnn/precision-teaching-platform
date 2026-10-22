"use strict";

// No browser or model service is needed for these loading/timer regressions.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const frontend = path.resolve(__dirname, "..");
const studentSource = fs.readFileSync(path.join(frontend, "student/student.js"), "utf8");
const teacherSource = fs.readFileSync(path.join(frontend, "app.js"), "utf8");

function loadFunctions(source, names, globals = {}) {
  const context = vm.createContext(globals);
  for (const name of names) {
    const match = source.match(new RegExp(`(?:^|\\n)(?:async )?function ${name}\\([^]*?\\n\\}`, "m"));
    assert.ok(match, `Function ${name} exists`);
    vm.runInContext(match[0], context);
  }
  return context;
}

function fakeDocument() {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, { hidden: false, inert: false, textContent: "", innerHTML: "", dataset: {} });
    return elements.get(id);
  };
  return { getElementById: get, querySelector: get };
}

const submittedSession = {
  status: "submitted", started_at: "2026-10-01T01:00:00+00:00", submitted_at: "2026-10-01T01:15:09+00:00"
};

test("submitted session time is frozen at submission, even weeks later", () => {
  const context = loadFunctions(studentSource, ["sessionElapsedSeconds"]);
  assert.equal(context.sessionElapsedSeconds(submittedSession, Date.parse("2026-10-22T01:00:00Z")), 909);
  assert.equal(context.sessionElapsedSeconds(submittedSession, Date.parse("2026-11-22T01:00:00Z")), 909);
});

test("active timer still advances and handles clock skew safely", () => {
  const context = loadFunctions(studentSource, ["sessionElapsedSeconds"]);
  const session = { status: "in_progress", started_at: "2026-10-01T01:00:00Z" };
  assert.equal(context.sessionElapsedSeconds(session, Date.parse("2026-10-01T01:02:03Z")), 123);
  assert.equal(context.sessionElapsedSeconds(session, Date.parse("2026-10-01T00:59:59Z")), 0);
});

test("missing or invalid timestamps never invent a submitted duration", () => {
  const context = loadFunctions(studentSource, ["sessionElapsedSeconds"]);
  assert.equal(context.sessionElapsedSeconds({ ...submittedSession, submitted_at: null }), null);
  assert.equal(context.sessionElapsedSeconds({ ...submittedSession, started_at: "invalid" }), null);
  assert.equal(context.sessionElapsedSeconds(null), null);
});

test("opening a submitted task cancels the previous interval and never schedules another", () => {
  const label = {};
  const cleared = [];
  let scheduled = 0;
  const state = { session: submittedSession, timerId: 77 };
  const context = loadFunctions(studentSource, ["sessionElapsedSeconds", "startTimer"], {
    state, $: () => label,
    window: { clearInterval: id => cleared.push(id), setInterval: () => { scheduled += 1; return 88; } }
  });
  context.startTimer();
  assert.deepEqual(cleared, [77]);
  assert.equal(state.timerId, null);
  assert.equal(scheduled, 0);
  assert.equal(label.textContent, "已用 15:09");
});

test("timer restarts on an active task and freezes immediately after submission", () => {
  const label = {};
  let scheduled = 0;
  const state = { session: { ...submittedSession, status: "in_progress" }, timerId: null };
  const context = loadFunctions(studentSource, ["sessionElapsedSeconds", "startTimer"], {
    state, $: () => label,
    window: { clearInterval: () => {}, setInterval: () => { scheduled += 1; return 88; } }
  });
  context.startTimer();
  assert.equal(state.timerId, 88);
  assert.equal(scheduled, 1);
  state.session = submittedSession;
  context.startTimer();
  assert.equal(state.timerId, null);
  assert.equal(scheduled, 1);
  assert.equal(label.textContent, "已用 15:09");
  const submitFlow = studentSource.slice(studentSource.indexOf('$("#confirmSubmitButton").addEventListener'));
  assert.match(submitFlow, /renderHistory\(\);\s+startTimer\(\);\s+startReportPolling\(\)/);
});

test("feedback stage status is independent of individual report review counts", () => {
  const document = fakeDocument();
  document.getElementById("analysisReviewStatus").textContent = "0/6 份已审核";
  const context = loadFunctions(teacherSource, ["renderFeedbackStageStatus"], { document });
  context.renderFeedbackStageStatus({ status: "confirmed" });
  assert.equal(document.getElementById("feedbackProgressStatus").textContent, "反馈阶段已确认");
  assert.equal(document.getElementById("analysisReviewStatus").textContent, "0/6 份已审核");
  context.renderFeedbackStageStatus({ status: "pending" });
  assert.equal(document.getElementById("feedbackProgressStatus").textContent, "阶段待确认");
});

test("teaching list and intervention placeholders explicitly identify feedback stage confirmation", () => {
  const html = fs.readFileSync(path.join(frontend, "index.html"), "utf8");
  assert.match(teacherSource, /feedbackDone \? "反馈阶段已确认" : feedbackOpen \? "阶段待确认"/);
  assert.match(html, /id="interventionEvidenceStatus">反馈阶段已确认/);
  assert.doesNotMatch(teacherSource, /"结果已确认"|"诊断结果已确认"|"反馈已确认"/);
  assert.doesNotMatch(html, />诊断结果已确认</);
});

test("report counts exclude mock and stale reports consistently", () => {
  const context = loadFunctions(teacherSource, ["studentReportCounts"]);
  const results = [
    { report: null },
    { report: { provider: "mock", status: "confirmed" } },
    { report: { provider: "mock", status: "pushed" } },
    { report: { provider: "openrouter", status: "stale" } },
    { report: { provider: "openrouter", status: "draft" } },
    { report: { provider: "openrouter", status: "confirmed" } },
    { report: { provider: "openrouter", status: "pushed" } }
  ];
  const counts = context.studentReportCounts(results);
  assert.equal(counts.generatedCount, 3);
  assert.equal(counts.reviewedCount, 2);
  assert.equal(counts.pushableCount, 1);
  assert.equal(context.studentReportCounts([]).generatedCount, 0);
});

test("in-flight or failed saves prevent pushing without changing report validity counts", () => {
  const context = loadFunctions(teacherSource, ["studentReportCounts"]);
  const results = [
    { report: { provider: "openrouter", status: "confirmed", _saving: true } },
    { report: { provider: "openrouter", status: "confirmed", _saveError: "网络错误" } }
  ];
  const counts = context.studentReportCounts(results);
  assert.equal(counts.generatedCount, 2);
  assert.equal(counts.reviewedCount, 2);
  assert.equal(counts.pushableCount, 0);
  const initialRender = teacherSource.match(/function renderStudentResults\([^]*?\n\}/)[0];
  const afterSave = teacherSource.match(/function refreshStudentReportReviewControls\([^]*?\n\}/)[0];
  for (const source of [initialRender, afterSave]) {
    assert.match(source, /studentReportCounts\(results\)/);
    assert.match(source, /\$\{generatedCount\}\/\$\{results\.length\} 份已生成/);
    assert.match(source, /\$\{reviewedCount\}\/\$\{results\.length\} 份已审核/);
  }
});

test("workspace HTML starts hidden and feedback placeholders do not retain demo students", () => {
  const document = fakeDocument();
  const context = loadFunctions(teacherSource, ["resetFeedbackLoading"], { document });
  document.getElementById("classEvidencePanel").innerHTML = "旧演示学生";
  document.getElementById("studentEvidencePanel").innerHTML = "旧演示报告";
  context.resetFeedbackLoading();
  for (const id of ["classEvidencePanel", "studentEvidencePanel"]) {
    assert.match(document.getElementById(id).innerHTML, /正在读取/);
    assert.doesNotMatch(document.getElementById(id).innerHTML, /旧演示/);
  }
  const html = fs.readFileSync(path.join(frontend, "index.html"), "utf8");
  assert.match(html, /<div class="app-shell" inert hidden>/);
  assert.match(html, /阶段确认不会自动确认或推送个体报告/);
});

test("teacher workspace stays hidden while bootstrap is pending", async () => {
  const document = fakeDocument();
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  const context = loadFunctions(teacherSource, ["showTeacherWorkspaceLoading", "initializeTeacherAuth"], {
    document,
    window: { PlatformAPI: { getTeacherAuthStatus: async () => ({ authenticated: true }) } },
    hydrateFromServer: () => pending,
    showToast: () => {}
  });
  const initialization = context.initializeTeacherAuth();
  await Promise.resolve();
  assert.equal(document.querySelector(".app-shell").hidden, true);
  assert.equal(document.querySelector(".app-shell").inert, true);
  assert.equal(document.getElementById("teacherAuthScreen").hidden, false);
  assert.equal(document.getElementById("teacherAuthLoading").textContent, "正在加载工作台…");
  finish();
  await initialization;
});

test("teacher bootstrap failures show a retry and never expose demo content", async () => {
  const document = fakeDocument();
  const context = loadFunctions(teacherSource, ["showTeacherWorkspaceLoading", "initializeTeacherAuth"], {
    document,
    window: { PlatformAPI: { getTeacherAuthStatus: async () => ({ authenticated: true }) } },
    hydrateFromServer: async () => { throw new Error("服务不可用"); },
    showToast: () => {}
  });
  await context.initializeTeacherAuth();
  assert.equal(document.querySelector(".app-shell").hidden, true);
  assert.equal(document.getElementById("teacherAuthScreen").hidden, false);
  assert.equal(document.getElementById("teacherAuthRetry").hidden, false);
  assert.match(document.getElementById("teacherAuthLoading").textContent, /加载失败：服务不可用/);
});

test("account login and password changes coexist with hidden-until-loaded workspace", async () => {
  const document = fakeDocument();
  let bootstrapCalls = 0;
  let passwordPrompts = 0;
  const context = loadFunctions(teacherSource, ["showTeacherWorkspaceLoading", "initializeTeacherAuth"], {
    document,
    window: { PlatformAPI: { getTeacherAuthStatus: async () => ({ authenticated: true, must_change_password: true }) } },
    hydrateFromServer: async () => { bootstrapCalls += 1; },
    showTeacherPasswordChange: () => { passwordPrompts += 1; },
    showToast: () => {}
  });
  await context.initializeTeacherAuth();
  assert.equal(passwordPrompts, 1);
  assert.equal(bootstrapCalls, 0);
  assert.equal(document.querySelector(".app-shell").hidden, true);
  assert.equal(document.querySelector(".app-shell").inert, true);
});

test("administrators are redirected without bootstrapping a teacher workspace", async () => {
  const document = fakeDocument();
  const destinations = [];
  let bootstrapCalls = 0;
  const context = loadFunctions(teacherSource, ["showTeacherWorkspaceLoading", "initializeTeacherAuth"], {
    document,
    window: {
      PlatformAPI: { getTeacherAuthStatus: async () => ({ authenticated: true, role: "admin" }) },
      location: { assign: url => destinations.push(url) }
    },
    hydrateFromServer: async () => { bootstrapCalls += 1; },
    showToast: () => {}
  });
  await context.initializeTeacherAuth();
  assert.deepEqual(destinations, ["/admin/"]);
  assert.equal(bootstrapCalls, 0);
  assert.equal(document.querySelector(".app-shell").hidden, true);
});

test("anonymous visitors see account login without obsolete school/name login calls", async () => {
  const document = fakeDocument();
  let loginPrompts = 0;
  const context = loadFunctions(teacherSource, ["showTeacherWorkspaceLoading", "initializeTeacherAuth"], {
    document,
    window: { PlatformAPI: { getTeacherAuthStatus: async () => ({ authenticated: false }) } },
    showTeacherAuth: () => { loginPrompts += 1; },
    showToast: () => {}
  });
  await context.initializeTeacherAuth();
  assert.equal(loginPrompts, 1);
  assert.doesNotMatch(teacherSource, /loginTeacher\(school, subject, name\)|loadTeacherLoginOptions/);
  assert.match(teacherSource, /loginTeacher\(username, password\)/);
  const html = fs.readFileSync(path.join(frontend, "index.html"), "utf8");
  assert.match(html, /id="teacherAuthUsername"/);
  assert.match(html, /id="teacherAuthPassword"[^>]*type="password"/);
  assert.match(html, /id="teacherPasswordForm" hidden/);
});

test("successful account login clears the password and waits for bootstrap before revealing content", async () => {
  const document = fakeDocument();
  let submit;
  let finish;
  let beganBootstrap;
  let credentials;
  const pending = new Promise(resolve => { finish = resolve; });
  const started = new Promise(resolve => { beganBootstrap = resolve; });
  document.getElementById("teacherAuthUsername").value = "  teacher-demo  ";
  document.getElementById("teacherAuthPassword").value = "test-only-password";
  document.getElementById("teacherAuthForm").addEventListener = (_, handler) => { submit = handler; };
  const context = loadFunctions(teacherSource, ["showTeacherWorkspaceLoading"], {
    document,
    window: { PlatformAPI: { loginTeacher: async (...args) => { credentials = args; return {}; } } },
    hydrateFromServer: () => { beganBootstrap(); return pending; },
    showTeacherAuth: () => assert.fail("Successful login must not show an error"),
    showToast: () => {}
  });
  const registration = teacherSource.match(/document\.getElementById\("teacherAuthForm"\)\.addEventListener\("submit", async event => \{[^]*?\n\}\);/);
  assert.ok(registration, "Account login handler exists");
  vm.runInContext(registration[0], context);
  const login = submit({ preventDefault: () => {} });
  await started;
  assert.deepEqual(credentials, ["teacher-demo", "test-only-password"]);
  assert.equal(document.getElementById("teacherAuthPassword").value, "");
  assert.equal(document.querySelector(".app-shell").hidden, true);
  assert.equal(document.querySelector(".app-shell").inert, true);
  assert.equal(document.getElementById("teacherAuthScreen").hidden, false);
  assert.equal(document.getElementById("teacherAuthSubmit").disabled, true);
  finish();
  await login;
  assert.equal(document.getElementById("teacherAuthSubmit").disabled, false);
});

test("both frontend entrypoints retain filing, notifications and math assets", () => {
  for (const relativePath of ["index.html", "student/index.html"]) {
    const html = fs.readFileSync(path.join(frontend, relativePath), "utf8");
    for (const asset of ["filing.css", "notifications.css", "notifications.js", "math-text.css", "math-text.js", "mathlive.min.js"]) {
      assert.ok(html.includes(asset), `${relativePath} includes ${asset}`);
    }
    assert.match(html, /京ICP备2026068374号-1/);
    assert.match(html, /京公网安备11010802050729号/);
    assert.doesNotMatch(html, /^(<<<<<<<|=======|>>>>>>>)/m);
  }
});

test("student result loading failures provide a retry instead of an endless placeholder", async () => {
  const document = fakeDocument();
  const context = loadFunctions(teacherSource, ["resetFeedbackLoading", "loadStudentResults"], {
    document, serverState: { current_teaching_id: "t1" },
    window: { PlatformAPI: { getStudentResults: async () => { throw new Error("服务不可用"); } } },
    console: { error: () => {} }, escapeHTML: value => value
  });
  context.resetFeedbackLoading();
  await context.loadStudentResults("t1");
  for (const id of ["classEvidencePanel", "studentEvidencePanel"]) {
    assert.match(document.getElementById(id).innerHTML, /诊断结果加载失败/);
    assert.match(document.getElementById(id).innerHTML, /data-retry-student-results/);
  }
  assert.equal(document.getElementById("analysisReviewStatus").textContent, "个体报告加载失败");
});

test("a polling failure preserves previously loaded reports", async () => {
  const document = fakeDocument();
  const context = loadFunctions(teacherSource, ["loadStudentResults"], {
    document, serverState: { current_teaching_id: "t1" },
    window: { PlatformAPI: { getStudentResults: async () => { throw new Error("暂时断网"); } } },
    console: { error: () => {} }
  });
  document.getElementById("classEvidencePanel").innerHTML = "已有班级报告";
  await context.loadStudentResults("t1");
  assert.equal(document.getElementById("classEvidencePanel").innerHTML, "已有班级报告");
});

test("student app becomes visible only after a real task has loaded", async () => {
  const document = fakeDocument();
  const loginView = { hidden: false };
  const studentApp = { hidden: true };
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  const context = loadFunctions(studentSource, ["enterApp"], {
    state: {}, loginView, studentApp, $: selector => document.querySelector(selector), subjectSelect: {},
    populateSubjects: () => {}, renderHistory: () => {}, openTask: () => pending
  });
  const initialization = context.enterApp({ student: { name: "测试学生", class_name: "测试班" }, tasks: [{ teaching_id: "t1", subject: "数学" }] });
  assert.equal(studentApp.hidden, true);
  assert.equal(loginView.hidden, false);
  finish();
  await initialization;
  assert.equal(studentApp.hidden, false);
  assert.equal(loginView.hidden, true);
});

test("student task initialization failures keep the app hidden", async () => {
  const document = fakeDocument();
  const loginView = { hidden: false };
  const studentApp = { hidden: true };
  const context = loadFunctions(studentSource, ["enterApp"], {
    state: {}, loginView, studentApp, $: selector => document.querySelector(selector), subjectSelect: {},
    populateSubjects: () => {}, renderHistory: () => {}, openTask: async () => { throw new Error("任务加载失败"); }
  });
  await assert.rejects(context.enterApp({ student: { name: "测试学生" }, tasks: [{ teaching_id: "t1" }] }), /任务加载失败/);
  assert.equal(studentApp.hidden, true);
  assert.equal(loginView.hidden, false);
  const html = fs.readFileSync(path.join(frontend, "student/index.html"), "utf8");
  assert.doesNotMatch(html, /基本不等式中的模型与论证|20 米围栏|林老师发布/);
});
