"use strict";

function readableApiDetail(detail, fallback) {
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return fallback;
  const fieldNames = {
    teaching_content_and_curriculum_analysis: "教学内容与课标分析",
    teaching_focus_and_difficulty_analysis: "教学重点与难点",
    teaching_environment_and_ai_support_conditions: "课堂与技术条件",
    planned_duration_minutes: "教学时间",
    content_analysis_confirmed: "教学内容与课标分析",
    focus_analysis_confirmed: "教学重点与难点",
    school_name: "学校",
    subject: "学科",
    teacher_name: "教师姓名"
  };
  const messages = detail.map(issue => {
    if (!issue || typeof issue !== "object") return "";
    const key = Array.isArray(issue.loc) ? issue.loc.at(-1) : "";
    const field = fieldNames[key] || (typeof key === "string" ? key : "");
    const message = typeof issue.msg === "string" ? issue.msg : "信息无效";
    if (message === "Field required" || issue.type === "missing") return `请填写${field || "必填信息"}`;
    if (issue.type === "string_too_short") return `请填写${field || "必填信息"}`;
    return field ? `${field}：${message}` : message;
  }).filter(Boolean);
  return messages.length ? [...new Set(messages)].join("；") : fallback;
}

window.PlatformAPI = {
  async request(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options
    });
    if (!response.ok) {
      let message = `请求失败（${response.status}）`;
      try {
        const data = await response.json();
        message = readableApiDetail(data.detail, message);
      } catch (_) {
        // Keep the status-based message for non-JSON failures.
      }
      if (response.status === 401 && !path.startsWith("/api/teacher-auth/")) {
        window.dispatchEvent(new Event("teacher-session-expired"));
      }
      throw new Error(message);
    }
    return response.json();
  },

  bootstrap() {
    return this.request("/api/bootstrap");
  },

  getTeacherAuthStatus() {
    return this.request("/api/teacher-auth/status");
  },

  getTeacherLoginOptions() {
    return this.request("/api/teacher-auth/login-options");
  },

  loginTeacher(schoolName, subject, teacherName) {
    return this.request("/api/teacher-auth/login", {
      method: "POST", body: JSON.stringify({ school_name: schoolName, subject, teacher_name: teacherName })
    });
  },

  logoutTeacher() {
    return this.request("/api/teacher-auth/logout", { method: "POST" });
  },

  saveProfile(profile) {
    return this.request("/api/teacher-profile", {
      method: "PUT",
      body: JSON.stringify(profile)
    });
  },

  createClassroom(classroom) {
    return this.request("/api/classrooms", { method: "POST", body: JSON.stringify(classroom) });
  },

  updateClassroom(classroomId, changes) {
    return this.request(`/api/classrooms/${encodeURIComponent(classroomId)}`, {
      method: "PUT", body: JSON.stringify(changes)
    });
  },

  createTeaching(teaching) {
    return this.request("/api/precision-teachings", {
      method: "POST",
      body: JSON.stringify(teaching)
    });
  },

  createTeachingDraft(teaching) {
    return this.request("/api/precision-teaching-drafts", {
      method: "POST", body: JSON.stringify(teaching)
    });
  },

  updateTeaching(teachingId, teaching, editIntent = null) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}`, {
      method: "PUT", body: JSON.stringify(editIntent ? { ...teaching, edit_intent: editIntent } : teaching)
    });
  },

  selectTeaching(teachingId) {
    return this.request("/api/current-teaching", {
      method: "PUT",
      body: JSON.stringify({ teaching_id: teachingId })
    });
  },

  deleteTeaching(teachingId) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}`, {
      method: "DELETE"
    });
  },

  getWorkspace(teachingId) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/workspace`);
  },

  getStudentResults(teachingId) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/student-results`);
  },

  regenerateClassReport(teachingId, classroomId) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/classrooms/${encodeURIComponent(classroomId)}/class-report/generate`, {
      method: "POST"
    });
  },

  getGoalPath(teachingId, classroomId) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/classrooms/${encodeURIComponent(classroomId)}/goal-path`);
  },

  generateTeacherAnalysis(teachingId, classroomId) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/classrooms/${encodeURIComponent(classroomId)}/goal-path/teacher-analysis/generate`, {
      method: "POST"
    });
  },

  saveTeacherAnalysis(teachingId, classroomId, input) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/classrooms/${encodeURIComponent(classroomId)}/goal-path/teacher-analysis`, {
      method: "PUT",
      body: JSON.stringify(input)
    });
  },

  generateGoalPath(teachingId, classroomId, input) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/classrooms/${encodeURIComponent(classroomId)}/goal-path/generate`, {
      method: "POST",
      body: JSON.stringify(input)
    });
  },

  saveGoalPath(teachingId, classroomId, result, confirmed = false) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/classrooms/${encodeURIComponent(classroomId)}/goal-path`, {
      method: "PUT",
      body: JSON.stringify({ result, confirmed })
    });
  },

  getActivityFormative(teachingId, classroomId) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/classrooms/${encodeURIComponent(classroomId)}/activity-formative`);
  },

  generateActivityFormative(teachingId, classroomId, requirements) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/classrooms/${encodeURIComponent(classroomId)}/activity-formative/generate`, {
      method: "POST",
      body: JSON.stringify({ teacher_activity_requirements: requirements })
    });
  },

  saveActivityFormative(teachingId, classroomId, result, confirmed = false, presentationOverrides = {}) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/classrooms/${encodeURIComponent(classroomId)}/activity-formative`, {
      method: "PUT",
      body: JSON.stringify({ result, confirmed, presentation_overrides: presentationOverrides })
    });
  },

  getIntegrationReport(teachingId, classroomId) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/classrooms/${encodeURIComponent(classroomId)}/integration-report`);
  },

  async downloadIntegrationReportPdf(teachingId, classroomId) {
    const response = await fetch(`/api/precision-teachings/${encodeURIComponent(teachingId)}/classrooms/${encodeURIComponent(classroomId)}/integration-report.pdf`);
    if (!response.ok) {
      let message = `PDF 导出失败（${response.status}）`;
      try {
        const data = await response.json();
        message = readableApiDetail(data.detail, message);
      } catch (_) {
        // Keep the status-based message for non-JSON failures.
      }
      if (response.status === 401) window.dispatchEvent(new Event("teacher-session-expired"));
      throw new Error(message);
    }
    return response.blob();
  },

  async downloadIntegrationReportDocx(teachingId, classroomId) {
    const response = await fetch(`/api/precision-teachings/${encodeURIComponent(teachingId)}/classrooms/${encodeURIComponent(classroomId)}/integration-report.docx`);
    if (!response.ok) {
      let message = `Word 导出失败（${response.status}）`;
      try {
        const data = await response.json();
        message = readableApiDetail(data.detail, message);
      } catch (_) {
        // Keep the status-based message for non-JSON failures.
      }
      if (response.status === 401) window.dispatchEvent(new Event("teacher-session-expired"));
      throw new Error(message);
    }
    return response.blob();
  },

  generateIntegrationReport(teachingId, classroomId) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/classrooms/${encodeURIComponent(classroomId)}/integration-report/generate`, {
      method: "POST"
    });
  },

  generateStudentReport(sessionId) {
    return this.request(`/api/student-results/${encodeURIComponent(sessionId)}/report/generate`, {
      method: "POST"
    });
  },

  saveStudentReport(sessionId, reportText, status = "draft", studentFeedbackText = null) {
    return this.request(`/api/student-results/${encodeURIComponent(sessionId)}/report`, {
      method: "PUT",
      body: JSON.stringify({ report_text: reportText, status,
        ...(studentFeedbackText === null ? {} : { student_feedback_text: studentFeedbackText }) })
    });
  },

  saveDiagnosis(teachingId, diagnosis) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/diagnosis`, {
      method: "PUT",
      body: JSON.stringify(diagnosis)
    });
  },

  getDiagnosticTaskRecommendations(teachingId) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/diagnostic-task-recommendations`);
  },

  generateDiagnosticTaskRecommendations(teachingId) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/diagnostic-task-recommendations/generate`, { method: "POST" });
  },

  generateRubric(teachingId) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/rubric/generate`, {
      method: "POST"
    });
  },

  saveRubric(teachingId, rubric, confirmed = false) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/rubric`, {
      method: "PUT",
      body: JSON.stringify({ rubric, confirmed })
    });
  },

  saveCustomAnalysisStandard(teachingId, standard) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/custom-analysis/standard`, {
      method: "PUT", body: JSON.stringify(standard)
    });
  },

  generateCustomAnalysis(teachingId, mode, subjectId) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/custom-analysis/${encodeURIComponent(mode)}/${encodeURIComponent(subjectId)}/generate`, {
      method: "POST"
    });
  },

  confirmFeedback(teachingId) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/feedback/confirm`, {
      method: "POST"
    });
  },

  saveIntervention(teachingId, intervention) {
    return this.request(`/api/precision-teachings/${encodeURIComponent(teachingId)}/intervention`, {
      method: "PUT",
      body: JSON.stringify(intervention)
    });
  },

  runSkill(skillKey, input) {
    return this.request("/api/ai/jobs", {
      method: "POST",
      body: JSON.stringify({ skill_key: skillKey, input })
    });
  }
};
