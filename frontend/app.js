"use strict";

const ui = {
  currentPage: "blocks",
  diagnosisStep: 2,
  interventionStep: 1,
  diagnosisType: "pre",
  rubricGenerating: false,
  allFeedbackPushed: false,
  selectedPrimaryPath: "progressive",
  activeInterventionActivity: "1",
  draggedInterventionActivity: null,
  activeStudentResultIndex: 0,
  activeClassroomId: null,
  activeInterventionClassroomId: null,
  interventionClassroomTeachingId: null,
  goalPathAppliedKey: null,
  activityFormativeDirty: false,
  activityFormativeError: null,
  diagnosticRecommendations: null,
  nextInterventionActivityId: 6
};

const pages = Array.from(document.querySelectorAll(".page"));
const toast = document.getElementById("toast");
let rubricSaveTimer;
const studentReportSaveQueues = new Map();
function renderDiagnosticRecommendations(result, status = "not_generated") {
  ui.diagnosticRecommendations = result?.diagnostic_task_design || null;
  const container = document.getElementById("taskProposals");
  const message = document.getElementById("taskRecommendationStatus");
  if (!container || !message) return;
  document.getElementById("regenerateTasks").textContent = ui.diagnosticRecommendations ? "AI 重新推荐" : "AI 推荐 3 个方案";
  if (!ui.diagnosticRecommendations) {
    container.replaceChildren();
    message.textContent = status === "stale"
      ? "精准教学信息已变化，请重新生成三个方案。"
      : "尚未生成。点击“AI 推荐 3 个方案”获取推荐；不会覆盖当前草稿。";
    return;
  }
  const design = ui.diagnosticRecommendations;
  message.textContent = `推荐方案：${design.candidates.find(item => item.candidate_id === design.recommended_candidate_id)?.name || ""}。${design.recommendation_rationale} 教师可选择任一方案。`;
  container.innerHTML = design.candidates.map((candidate, index) => {
    const recommended = candidate.candidate_id === design.recommended_candidate_id;
    const rules = candidate.dialogue_rules || { required: [], prohibited: [] };
    return `<article class="proposal" data-proposal="${escapeHTML(candidate.candidate_id)}">
      <div class="proposal-top"><span>方案 ${index + 1} · ${escapeHTML(candidate.externalization_focus)}</span>${recommended ? "<b>推荐</b>" : ""}</div>
      <h3>${escapeHTML(candidate.name)}</h3>
      <p>${escapeHTML(candidate.task_content)}</p>
      <dl><div><dt>互动方式</dt><dd>${escapeHTML(candidate.interaction_mode)}</dd></div><div><dt>预计时长</dt><dd>${candidate.estimated_duration_minutes} 分钟</dd></div></dl>
      <details><summary>查看 AI 角色与对话规则</summary><p><b>角色：</b>${escapeHTML(candidate.ai_role)}</p><p><b>应遵循：</b>${rules.required.map(escapeHTML).join("；")}</p><p><b>禁止：</b>${rules.prohibited.map(escapeHTML).join("；")}</p></details>
      <button type="button" class="button small" data-select-proposal="${escapeHTML(candidate.candidate_id)}">采用此方案</button>
    </article>`;
  }).join("");
}

async function loadDiagnosticRecommendations(teachingId) {
  try {
    const response = await window.PlatformAPI.getDiagnosticTaskRecommendations(teachingId);
    if (serverState?.current_teaching_id === teachingId) renderDiagnosticRecommendations(response.result, response.status);
  } catch (error) {
    if (serverState?.current_teaching_id === teachingId) {
      document.getElementById("taskRecommendationStatus").textContent = `无法加载推荐：${error.message}`;
    }
  }
}

const interventionPathContent = {
  progressive: {
    name: "递进推进型",
    diagram: "基础建立 → 支持 → 建构 → 内化 → 迁移",
    brief: "本课以递进推进型为主要路径。先利用诊断证据建立共同目标，再按照学生当前思维结构提供不同强度的支持；通过解释、质疑和整合建立关键关系；随后撤除支架进行独立复诊，并根据新证据进入迁移或补充支持。",
    details: [
      ["适用情形", "全班围绕同一核心目标学习，但学生处在不同思维结构水平，需要沿相邻层级逐步进阶。"],
      ["课堂结构", "共同定向—分层支持—关系建构—独立内化—迁移检验。阶段按顺序推进，分层支持阶段可设置多个小组并行。"],
      ["组织与支持", "全班保持共同主线；P/U学生侧重要素识别，M学生侧重关系建构，R学生侧重迁移与反思。支架随证据改善逐步撤除。"],
      ["诊断与调节", "在关系建构和独立应用后设置检查点。达到标准则进入下一阶段，未达到则补充支持，证据不足则先补充表达。"],
      ["融合建议", "可融合短时同质分组或少数学生的嵌入式支持，但不要把层级变成固定学生标签。"]
    ]
  },
  embedded: {
    name: "主线嵌入型",
    diagram: "共同主线 ↘ 靶向支持 ↗ 回归主线",
    brief: "本课保持全班共同主线。当少数学生在关键活动中持续缺少必要基础或关键关系时，安排短时教师或工具支持；达到回归标准后立即返回共同任务，避免形成固定分组。",
    details: [
      ["适用情形", "大多数学生可以沿共同主线学习，只有少数学生在某个关键关系或方法上需要短时支持。"],
      ["课堂结构", "共同学习—形成性检查—短时支持分支—达到标准后回归共同任务。支持分支嵌入主线，不另起一套长期课程。"],
      ["组织与支持", "教师、同伴或AI临时支持少数学生，其余学生继续完成深化任务。支持对象依据当堂证据动态确定。"],
      ["诊断与调节", "必须写清进入分支和返回主线的条件，避免学生因一次判断被长期留在支持组。"],
      ["融合建议", "适合与递进推进或个体练习结合；课堂时间紧张时，支持分支应短、小、目标明确。"]
    ]
  },
  parallel: {
    name: "并行分层型",
    diagram: "共同定向 → 并行学习 → 共同汇聚",
    brief: "本课先明确共同核心目标，再依据诊断结果设置临时并行任务。不同小组围绕同一思维目标分别完成要素识别、关系建构或迁移检验，形成成果后重新汇合，通过交流和独立作答确认进阶。",
    details: [
      ["适用情形", "班级同时存在几类较明确的学习需要，单一任务或同一种支架难以让不同学生都获得有效进阶。"],
      ["课堂结构", "共同定向—多个临时小组同时学习—成果交流—共同任务或独立检验。并行的是任务与支架，共同的是核心目标和成果标准。"],
      ["组织与支持", "可采用同质小组：P/U组识别要素，M组建立关系，R组检验迁移；也可按具体障碍重新分组。"],
      ["诊断与调节", "各组用同一核心标准提交成果。汇聚后再用个人证据判断是否进阶，必要时重新分组。"],
      ["融合建议", "可在某个并行组中加入教师短时支持、学习站或AI工具，不要求整节课始终分层。"]
    ]
  },
  stations: {
    name: "站点轮转型",
    diagram: "共同定向 → 功能分站 → 汇聚整合",
    brief: "本课围绕共同目标设置具有不同功能的学习站，包括教师指导、同伴讨论、AI支持和独立任务。学生依据诊断需要进入相应站点，在获得目标证据后转入下一站或回到全班整合。",
    details: [
      ["适用情形", "教学资源与任务类型较丰富，教师希望同时组织直接指导、同伴解释、AI练习和独立学习。"],
      ["课堂结构", "共同定向—进入功能站点—按规则轮转或转站—全班汇聚。站点可以同时运行，但不要求每名学生机械经过全部站点。"],
      ["组织与支持", "每个站点只有一个清晰功能，如教师指导站、关系建构站、迁移挑战站和独立检验站。"],
      ["诊断与调节", "根据站点成果决定继续、转站或回到全班；写清每站的产出、时间、人数和转站条件。"],
      ["融合建议", "适合学生已熟悉轮转规则且课堂资源充足的情形。首次使用时宜减少站点数量。"]
    ]
  },
  individual: {
    name: "个体自驱型",
    diagram: "共同定向 → 个体循环 → 汇聚提升",
    brief: "本课在共同定向后为学生提供个体学习回路。学生持续完成表达、获得支架、修改理解和再次尝试；系统与教师依据过程证据调节支持强度，最后回到全班分享与提升。",
    details: [
      ["适用情形", "学生需要不同进度、不同练习次数或个别支架，且课堂能够提供个人任务单或数字化工具。"],
      ["课堂结构", "共同定向—个人表达—获得适度支持—修改并再次尝试—独立检验—全班汇聚。"],
      ["组织与支持", "教师巡视并关注关键学生；AI或任务单只提供条件性支架，不代替学生思考和教师判断。"],
      ["诊断与调节", "依据每次作品或表达决定支架保持、减弱或撤除，并保留最后一次独立作答作为进阶证据。"],
      ["融合建议", "适合作为某个课堂阶段，不宜让整节课完全失去共同对话和同伴建构。"]
    ]
  }
};

const interventionFusionLabels = {
  embedded: "少数学生短时支持",
  parallel: "关键阶段并行分层",
  collaborative: "异质协同建构",
  stations: "功能学习站",
  individual: "个体AI学习回路"
};

const interventionHelpContent = {
  "teaching-context": {
    title: "教学情境与条件",
    sections: [
      ["AI 草稿", "平台结合当前精准教学资料和班级报告起草教学内容、课标关联、教学重点与难点；没有课标原文时会提示教师核对，不引用虚构条款。"],
      ["教师确认", "教师修改并确认前两项分析，填写教学时长、课堂与技术条件，再进入目标与路径生成。"]
    ]
  },
  "content-standard": {
    title: "教学内容与课标分析",
    sections: [
      ["教学内容", "说明本节课涉及的核心概念、方法、知识关系及其在单元中的位置，突出学生需要理解和运用的关键内容。"],
      ["课标与素养", "结合课程标准说明学习要求，并指出本内容主要发展哪些学科核心素养。不要大段复制课标原文，应写出与本课设计直接相关的要求。"]
    ]
  },
  "focus-difficulty": {
    title: "教学重点与难点",
    sections: [
      ["填写依据", "教学重点和难点可以结合诊断结果分析：重点应回应本课核心目标，难点应指向诊断中多数学生真实出现的思维障碍。"],
      ["建议写法", "分别写清学生需要建立的关键关系，以及容易出现的典型困难，避免只写知识点名称。"]
    ]
  },
  "teaching-time": {
    title: "教学时间",
    sections: [
      ["设置含义", "填写本次精准干预方案计划使用的总时长，单位为分钟。"],
      ["如何使用", "AI将按照总时长生成活动序列并分配每个活动的时间。教师修改总时长后，需要重新生成或自行调整活动时间。"]
    ]
  },
  "classroom-technology": {
    title: "课堂与技术条件",
    sections: [
      ["填写内容", "说明班额、分组条件、可用空间、投影或平板数量、纸质材料以及教师能够获得的即时学习数据。"],
      ["注意事项", "只填写会实际影响活动组织和资源设计的条件，不需要重复教学时间。"]
    ]
  },
  "goal-design": {
    title: "目标设计",
    sections: [
      ["共同核心目标", "明确全班围绕同一思维目标学习，避免把差异化变成彼此无关的教学任务。"],
      ["进阶目标", "根据学生当前表现确定邻近一步的提升目标，并用课堂中可观察的行为说明是否达成。证据不足的学生先以补充表达证据为目标。"]
    ]
  },
  "path-design": {
    title: "活动路径",
    sections: [
      ["路径原型", "五种路径是组织课堂推进的参考结构，不是必须完整照搬的固定流程。"],
      ["设计方法", "选择一个最能体现课堂主线的主要路径，再融合最多两项其他组织机制。教师可修改路径设计要求，由AI据此生成具体活动序列。"],
      ["评价节点", "路径中的检查点用于决定继续、补充支持、重新分组或进入迁移，不是额外附加的测验。"]
    ]
  },
  "activity-design": {
    title: "学习活动设计",
    sections: [
      ["设计单位", "课堂由若干按顺序推进的阶段组成。每个阶段说明目标、任务、支持、组织、会话和评价；同一阶段内可设置多个并行小组或学习站。"],
      ["教师调整", "AI先生成整套阶段与分支，教师再调整顺序、时长和具体设计。每个活动都要能产生可观察的学生思维证据。"]
    ]
  },
  checkpoint: {
    title: "形成性诊断",
    sections: [
      ["设置位置", "只放在需要决定后续教学方向的关键活动之后，不必每个活动都设置。"],
      ["必须说明", "明确判断问题、证据来源和判断标准，并分别设计达到、未达到和证据不足时的后续行动。"]
    ]
  },
  "resource-design": {
    title: "资源与工具设计",
    sections: [
      ["生成依据", "只为已经确认的学习活动准备必要资源，资源内容、难度与支架强度应对应学生的进阶目标。"],
      ["AI工具边界", "教师决定是否使用AI、用于哪个活动以及何时介入。AI提供条件性支持，不替代教师决策或学生思考。"]
    ]
  }
};

const interventionActivities = {
  "1": {
    title: "比较诊断证据，明确完整论证标准",
    structure: "全班共同活动",
    goalLevels: ["M", "R"],
    goal: "学生能够区分“得到一个数值”和“证明最大值”，并识别完整论证所需的关键关系。",
    task: "比较三份诊断作答，标出已有要素、已建立关系和仍缺少的论证依据。",
    implementation: "教师呈现匿名典型作答，学生先独立标注，再与同伴比较。全班共同归纳约束、方法、取等条件和结论之间需要建立的关系。",
    support: "为P/U学生提供要素清单；M学生使用关系箭头补全论证；R/EA学生判断三份作答的证据是否充分。",
    organization: "全班",
    dialogue: "师生",
    checkpoint: true,
    checkpointQuestion: "学生能否指出完整论证所需的关键关系？",
    checkpointEvidence: "个人比较标记和随机口头解释。",
    checkpointStandard: "至少指出一个关键关系并说明其作用；表达不足则保留为证据不足。",
    reached: "依据课前诊断进入相应进阶任务。",
    notReached: "教师用典型作答进行短时示范后再次判断。",
    insufficient: "使用中性追问补充表达，不直接归类。"
  },
  "2": {
    title: "按诊断结果完成不同支持任务",
    structure: "同质小组并行",
    goalLevels: ["M", "R", "EA"],
    goal: "不同学生在共同核心目标下完成邻近一步的思维进阶。",
    task: "P/U组识别任务要素，M组重组论证卡并绘制关系图，R/EA组完成条件变式与迁移检验。",
    implementation: "教师说明各组任务和共同成果标准。学生先独立思考，再在临时同质小组中完成任务；教师重点支持基础组，完成后学生回到共同成果格式。",
    support: "P/U组使用要素卡和情境图；M组使用可渐退的关系框架；R/EA组只提供新的条件和反例，不提供步骤。",
    organization: "同质小组",
    dialogue: "生生",
    checkpoint: false,
    checkpointQuestion: "",
    checkpointEvidence: "",
    checkpointStandard: "",
    reached: "",
    notReached: "",
    insufficient: ""
  },
  "3": {
    title: "解释、质疑并整合不同路径成果",
    structure: "异质小组协同",
    goalLevels: ["R"],
    goal: "学生通过解释与质疑，把多个相关要素整合为有条件、有依据的完整论证。",
    task: "异质小组交换要素清单、关系图和迁移结论，共同形成一份完整论证并保留修改痕迹。",
    implementation: "每位学生先说明本组成果，同伴只围绕依据、条件和关系追问。小组根据质疑修改论证，教师选择典型关系进行全班讨论。",
    support: "提供“你的依据是什么”“这个条件与结论怎样联系”等追问卡；仅当小组持续无新关系时，课堂AI进行条件性介入。",
    organization: "异质小组",
    dialogue: "生生机",
    checkpoint: true,
    checkpointQuestion: "学生能否独立形成包含取等条件的完整论证？",
    checkpointEvidence: "小组论证修改痕迹和两分钟个人书面解释。",
    checkpointStandard: "能够连接约束、基本不等式、取等条件与最大值结论，并说明关系。",
    reached: "撤除关系支架，进入独立同构任务。",
    notReached: "保留关系图，进行短时再教学后重新作答。",
    insufficient: "补充个人表达证据后再判断。"
  },
  "4": {
    title: "撤除支架，独立完成同构问题论证",
    structure: "个体独立活动",
    goalLevels: ["R", "EA"],
    goal: "学生在撤除过程支架后独立形成可与课前诊断比较的新证据。",
    task: "独立解决表面情境不同但关系结构相同的最值问题，提交模型、理由、取等条件和结论。",
    implementation: "学生独立完成退出卡，教师不进行方向性提示。完成后按成功标准自检并提交。",
    support: "仅保留共同成功标准；尚未达到的学生可以保留关系图，但不提供步骤和答案。",
    organization: "个体",
    dialogue: "学生—内容",
    checkpoint: false,
    checkpointQuestion: "",
    checkpointEvidence: "",
    checkpointStandard: "",
    reached: "",
    notReached: "",
    insufficient: ""
  },
  "5": {
    title: "比较学习成果并总结论证结构",
    structure: "全班共同活动",
    goalLevels: ["R", "EA"],
    goal: "学生回看自己的思维变化，形成可迁移的最值论证结构。",
    task: "比较课前表达与课堂退出卡，说明自己新增或重新建立的关系，并概括完整论证结构。",
    implementation: "教师展示两份典型成果，学生先写个人变化，再进行全班分享。教师总结共性进步、遗留问题和下一步学习方向。",
    support: "提供简短反思句式；教师分别指出要素补充、关系建构和迁移检验的后续方向。",
    organization: "全班",
    dialogue: "师生",
    checkpoint: false,
    checkpointQuestion: "",
    checkpointEvidence: "",
    checkpointStandard: "",
    reached: "",
    notReached: "",
    insufficient: ""
  }
};
let toastTimer;
let serverState = null;

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2300);
}

function setBackendStatus(state, label) {
  const status = document.getElementById("backendStatus");
  if (!status) return;
  status.className = `demo-badge backend-status ${state}`;
  status.textContent = label;
}

function updateTeacherIdentity(profile) {
  const name = profile.display_name || "教师";
  document.getElementById("teacherDisplayName").textContent = name;
  document.querySelector("#teacherProfileButton .avatar").textContent = name.slice(0, 1);
  document.getElementById("profileDisplayName").value = name;
  document.getElementById("profileSubject").value = profile.subject || "";
  document.getElementById("profileYears").value = profile.years_experience ?? 0;
  document.getElementById("profileTeachingStyle").value = profile.teaching_style || "";
  syncStyleSuggestions();
  document.querySelector(".profile-reference b").textContent = profile.teaching_style || "尚未填写教学风格";
  document.querySelector(".profile-reference > span").textContent = profile.teaching_style ? "已引用教师档案" : "教师档案待完善";
}

function syncStyleSuggestions() {
  const value = document.getElementById("profileTeachingStyle").value;
  document.querySelectorAll("[data-style-suggestion]").forEach(button => {
    const added = value.includes(button.textContent.trim());
    button.classList.toggle("is-added", added);
    button.setAttribute("aria-label", `${button.textContent.trim()}${added ? "（已填写）" : "（点击添加）"}`);
  });
}

function renderClassrooms(classrooms) {
  const list = document.getElementById("classroomList");
  list.replaceChildren();
  renderPublishClassrooms();
  renderProfileSetup();
  if (!classrooms.length) {
    const empty = document.createElement("p");
    empty.className = "class-profile-empty";
    empty.textContent = "还没有班级。点击下方“添加班级”建立当前教师的班级。";
    list.append(empty);
    return;
  }
  classrooms.forEach(classroom => {
    const card = document.createElement("div");
    card.className = "class-profile";
    const header = document.createElement("header");
    const name = document.createElement("b");
    name.textContent = classroom.name;
    const count = document.createElement("span");
    count.textContent = `${classroom.student_count} 人`;
    header.append(name, count);
    const background = document.createElement("p");
    background.textContent = classroom.background;
    const edit = document.createElement("button");
    edit.className = "text-button";
    edit.type = "button";
    edit.textContent = "编辑班级背景";
    edit.dataset.editClassroom = classroom.id;
    card.append(header, background, edit);
    list.append(card);
  });
}

function needsProfileSetup() {
  return Boolean(serverState && (!serverState.teacher?.profile_completed || !serverState.classrooms?.length));
}

function renderProfileSetup() {
  if (!serverState) return;
  const needsProfile = !serverState.teacher?.profile_completed;
  const needsClass = !serverState.classrooms?.length;
  const notice = document.getElementById("profileSetupNotice");
  notice.hidden = !needsProfile && !needsClass;
  if (!notice.hidden) {
    notice.textContent = needsProfile && needsClass
      ? "首次使用：请先保存教师教学信息，再添加至少一个班级。完成后即可开启精准教学。"
      : needsProfile ? "请保存教师教学信息，完成首次设置。" : "教师信息已保存，请添加至少一个班级。";
  }
  document.getElementById("newTeachingButton").hidden = needsProfile || needsClass;
}

function renderPublishClassrooms() {
  const list = document.getElementById("publishClassOptions");
  if (!list) return;
  list.replaceChildren();
  const classrooms = serverState?.classrooms || [];
  const published = new Set(serverState?.current_workspace?.diagnosis?.classroom_ids || []);
  if (!classrooms.length) {
    const empty = document.createElement("p");
    empty.className = "class-options-empty";
    empty.textContent = "还没有班级，请先在“教师与班级背景信息”中添加班级。";
    list.append(empty);
    return;
  }
  classrooms.forEach(classroom => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = "publishClassroom";
    input.value = classroom.id;
    input.checked = published.has(classroom.id);
    label.append(input, document.createTextNode(`${classroom.name} · ${classroom.student_count} 人`));
    list.append(label);
  });
}

function setupClassroomCreation() {
  const button = document.querySelector("#page-profile .profile-card:nth-child(2) .button.full");
  if (!button || button.dataset.ready) return;
  button.id = "addClassroomButton";
  button.dataset.ready = "true";
  button.addEventListener("click", () => {
    button.hidden = true;
    const form = document.createElement("form");
    form.className = "add-classroom-form";
    form.innerHTML = `<label>班级名称<input name="name" maxlength="80" placeholder="例如：高一（3）班" required></label>
      <label>学生人数<input name="student_count" type="number" min="0" max="1000" value="0" required></label>
      <label>班级背景（可选）<textarea name="background" rows="3" maxlength="2000"></textarea></label>
      <div><button class="button primary" type="submit">保存班级</button><button class="button" type="button" data-cancel>取消</button></div>`;
    button.before(form);
    form.querySelector("[name=name]").focus();
    form.querySelector("[data-cancel]").addEventListener("click", () => { form.remove(); button.hidden = false; });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const submit = form.querySelector("[type=submit]");
      submit.disabled = true;
      try {
        const classroom = await window.PlatformAPI.createClassroom({
          name: form.querySelector('[name="name"]').value.trim(),
          student_count: Number(form.querySelector('[name="student_count"]').value || 0),
          background: form.querySelector('[name="background"]').value.trim()
        });
        serverState.classrooms.push(classroom);
        renderClassrooms(serverState.classrooms);
        renderTeachingList(serverState.precision_teachings);
        form.remove();
        button.hidden = false;
        showToast("班级已添加到当前教师空间");
      } catch (error) {
        showToast(`添加班级失败：${error.message}`);
        submit.disabled = false;
      }
    });
  });
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatUpdatedAt(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}

function formatFileSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function parseTeacherReportSections(markdown) {
  const source = String(markdown || "").replaceAll("\r\n", "\n");
  const heading = /^#{1,3}[ \t]*(?:[1-6一二三四五六][.．、][ \t]*)?(学生信息|SOLO\s*诊断结果与依据|当前思维结构特征|主要问题与进阶障碍|进阶目标|后续学习策略)[ \t]*$/gim;
  const matches = [...source.matchAll(heading)];
  if (matches.length !== 6) return [{ title: "教师可读报告", body: source, bodyStart: 0, end: source.length }];
  return matches.map((match, index) => ({
    title: match[1].replace(/\s+/g, " ").trim(),
    body: source.slice(match.index + match[0].length, matches[index + 1]?.index ?? source.length).trim(),
    bodyStart: match.index + match[0].length,
    end: matches[index + 1]?.index ?? source.length
  }));
}

function renderReportInline(value) {
  return escapeHTML(value).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
}

function renderTeacherReportMarkdown(markdown) {
  const output = [];
  let list = "";
  let paragraph = [];
  let code = [];
  let inCode = false;
  const closeParagraph = () => {
    if (paragraph.length) output.push(`<p>${paragraph.map(renderReportInline).join("<br>")}</p>`);
    paragraph = [];
  };
  const closeList = () => {
    if (list) output.push(`</${list}>`);
    list = "";
  };
  for (const line of String(markdown || "").split("\n")) {
    if (/^\s*```/.test(line)) {
      closeParagraph(); closeList();
      if (inCode) { output.push(`<pre>${escapeHTML(code.join("\n"))}</pre>`); code = []; }
      inCode = !inCode;
      continue;
    }
    if (inCode) { code.push(line); continue; }
    const trimmed = line.trim();
    if (!trimmed) { closeParagraph(); closeList(); continue; }
    const subheading = trimmed.match(/^#{3,5}\s+(.+)$/);
    if (subheading) { closeParagraph(); closeList(); output.push(`<h4>${renderReportInline(subheading[1])}</h4>`); continue; }
    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    const numbered = trimmed.match(/^\d+[.、]\s+(.+)$/);
    if (bullet || numbered) {
      closeParagraph();
      const type = bullet ? "ul" : "ol";
      if (list !== type) { closeList(); output.push(`<${type}>`); list = type; }
      output.push(`<li>${renderReportInline((bullet || numbered)[1])}</li>`);
      continue;
    }
    closeList();
    if (trimmed.startsWith("> ")) { closeParagraph(); output.push(`<blockquote>${renderReportInline(trimmed.slice(2))}</blockquote>`); }
    else paragraph.push(trimmed);
  }
  closeParagraph(); closeList();
  if (code.length) output.push(`<pre>${escapeHTML(code.join("\n"))}</pre>`);
  return output.join("");
}

function renderTeacherReportSections(reportText) {
  return parseTeacherReportSections(reportText).map((section, index) => `<section class="teacher-report-section"><h3>${escapeHTML(section.title)}</h3><div class="teacher-report-view" data-edit-report-section="${index}" role="button" tabindex="0" aria-label="编辑${escapeHTML(section.title)}" title="点击编辑，离开后保存">${renderTeacherReportMarkdown(section.body)}</div></section>`).join("");
}

function refreshStudentReportReviewControls(result) {
  if (currentStudentResult()?.id !== result.id) return;
  const report = result.report;
  const card = document.querySelector("#studentResultDetail .individual-report-card");
  if (!card) return;
  const status = card.querySelector(".card-heading .status");
  status.textContent = report._saveError ? "保存失败" : report._saving ? "正在保存" : report.status === "draft" ? "待教师确认" : report.status === "confirmed" ? "已确认" : report.status === "pushed" ? "已推送" : "需要重新生成";
  status.className = `status ${report._saveError || report.status === "stale" ? "warning" : ["confirmed", "pushed"].includes(report.status) ? "success" : "neutral"}`;
  const saveState = card.querySelector("#feedbackAutosaveState");
  saveState.textContent = report._saveError ? "保存失败，请再次编辑重试" : report._saving ? "正在自动保存…" : report.status === "stale" ? "上游内容已变化" : "已保存";
  const confirm = card.querySelector("#pushStudentFeedback");
  confirm.dataset.reportStatus = report.status === "confirmed" ? "pushed" : "confirmed";
  confirm.textContent = report.provider === "mock" ? "Mock 报告不可确认" : report.status === "confirmed" ? "推送给学生" : report.status === "pushed" ? "已推送" : "确认报告";
  confirm.disabled = report.provider === "mock" || report.status === "stale" || report.status === "pushed" || Boolean(report._saving || report._saveError);
  const selected = document.querySelector("#studentResultList > button.active em");
  if (selected) selected.textContent = report.status === "draft" ? "待确认" : report.status === "confirmed" ? "已确认" : report.status === "pushed" ? "已推送" : "需重生成";
  const results = (serverState.studentResults?.results || []).filter(item => item.student.classroom_id === ui.activeClassroomId);
  const pushableCount = results.filter(item => item.report?.status === "confirmed" && !item.report._saving && !item.report._saveError && item.report.provider !== "mock").length;
  const toolbarCount = document.querySelector("#studentEvidencePanel .student-feedback-toolbar span");
  if (toolbarCount) toolbarCount.textContent = `${results.filter(item => item.report).length}/${results.length} 份已生成 · ${pushableCount} 份已确认待推送`;
  const bulkPush = document.getElementById("pushConfirmedStudentReports");
  if (bulkPush) bulkPush.disabled = !pushableCount;
}

function startTeacherReportSectionEdit(view) {
  const result = currentStudentResult();
  if (!result?.report || view.closest(".teacher-report-section")?.querySelector("textarea")) return;
  if (result.report.status === "stale") { showToast("上游内容已变化，请先重新生成报告"); return; }
  const index = Number(view.dataset.editReportSection);
  const section = parseTeacherReportSections(result.report.report_text)[index];
  if (!section) return;
  const editor = document.createElement("textarea");
  editor.className = "teacher-report-inline-editor";
  editor.setAttribute("aria-label", `编辑${section.title}`);
  editor.value = section.body;
  editor.rows = Math.min(18, Math.max(6, section.body.split("\n").length + 2));
  view.replaceWith(editor);
  editor.focus();
  editor.addEventListener("keydown", event => {
    if (event.key === "Escape") { editor.dataset.cancel = "true"; editor.blur(); }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") editor.blur();
  });
  editor.addEventListener("blur", async () => {
    const oldText = result.report.report_text.replaceAll("\r\n", "\n");
    const currentSection = parseTeacherReportSections(oldText)[index];
    const body = editor.dataset.cancel ? currentSection.body : editor.value.trim();
    const display = document.createElement("div");
    display.className = "teacher-report-view";
    display.dataset.editReportSection = String(index);
    display.setAttribute("role", "button");
    display.tabIndex = 0;
    display.setAttribute("aria-label", `编辑${currentSection.title}`);
    display.title = "点击编辑，离开后保存";
    display.innerHTML = renderTeacherReportMarkdown(body);
    editor.replaceWith(display);
    if (body === currentSection.body) return;
    const nextText = currentSection.bodyStart === 0
      ? body
      : `${oldText.slice(0, currentSection.bodyStart)}\n\n${body}\n\n${oldText.slice(currentSection.end).trimStart()}`.trim();
    result.report.report_text = nextText;
    result.report.status = "draft";
    result.report._saving = true;
    result.report._saveError = false;
    refreshStudentReportReviewControls(result);
    const previous = studentReportSaveQueues.get(result.id) || Promise.resolve();
    const save = previous.catch(() => {}).then(() => window.PlatformAPI.saveStudentReport(result.id, nextText, "draft"));
    studentReportSaveQueues.set(result.id, save);
    try {
      const updated = await save;
      if (result.report.report_text === nextText) result.report = updated.report;
    } catch (error) {
      if (result.report.report_text === nextText) { result.report._saving = false; result.report._saveError = true; }
      showToast(`报告保存失败：${error.message}`);
    } finally {
      if (studentReportSaveQueues.get(result.id) === save) studentReportSaveQueues.delete(result.id);
      refreshStudentReportReviewControls(result);
    }
  }, { once: true });
}

function renderStudentResultDetail(result) {
  const panel = document.getElementById("studentResultDetail");
  if (!panel || !result) return;
  const studentMessages = result.messages.filter(message => message.role === "student").length;
  const report = result.report;
  const reportStatus = report?._saveError ? "保存失败" : report?._saving ? "正在保存" : ({ draft: "待教师确认", confirmed: "已确认", pushed: "已推送", stale: "需要重新生成" }[report?.status] || "尚未生成");
  const reportStatusClass = report?._saveError || report?.status === "stale" ? "warning" : report?.status === "pushed" || report?.status === "confirmed" ? "success" : "neutral";
  const reportHasNewSections = report ? parseTeacherReportSections(report.report_text).length === 6 : false;
  const rubricReady = serverState.current_workspace?.rubric?.status === "confirmed";
  const submittedWork = result.submitted_text
    ? `<blockquote class="submitted-text-evidence">${escapeHTML(result.submitted_text)}</blockquote>`
    : `<p class="muted">本次未提交文字成果；生成报告时仅使用完整对话。</p>`;
  const reportCard = !report ? `<article class="card individual-report-card report-empty-card">
      <div class="card-heading"><div><span class="section-kicker">2 · 个体反馈报告</span><h2>${escapeHTML(result.student.name)}的个体反馈报告</h2><p>依据已确认量规和完整会话${result.submitted_text ? "，结合文字成果" : ""}生成。</p></div><span class="status neutral">尚未生成</span></div>
      <div class="stage-empty-state"><span>SOLO 个体诊断</span><h2>生成学生报告</h2>${rubricReady ? "" : '<p>请先返回诊断设计，在“分析标准”步骤确认当前任务量规。</p>'}<button class="button primary" data-generate-student-report="${escapeHTML(result.id)}" ${rubricReady ? "" : "disabled"}>${rubricReady ? "生成报告" : "等待量规确认"}</button></div>
    </article>` : `<article class="card individual-report-card">
      <div class="card-heading"><div><span class="section-kicker">2 · 个体反馈报告</span><h2>${escapeHTML(result.student.name)}的个体反馈报告</h2><p>${reportHasNewSections ? "按新版六部分展示；点击任一章节内容即可编辑，离开后自动保存。" : "这份报告采用旧版结构；重新生成后会按新版六部分展示，当前可点击编辑整篇。"}</p></div><span class="status ${reportStatusClass}">${reportStatus}</span></div>
      <div class="teacher-report-sections">${renderTeacherReportSections(report.report_text)}</div>
      <div class="report-footer"><span class="autosave-state" id="feedbackAutosaveState">${report._saveError ? "保存失败，请再次编辑重试" : report._saving ? "正在自动保存…" : report.status === "stale" ? "上游内容已变化" : "已保存"}</span><div><button class="button" data-generate-student-report="${escapeHTML(result.id)}">重新生成</button><button class="button primary" id="pushStudentFeedback" data-report-status="${report.status === "confirmed" ? "pushed" : "confirmed"}" ${report.provider === "mock" || report.status === "stale" || report.status === "pushed" || report._saveError || report._saving ? "disabled" : ""}>${report.provider === "mock" ? "Mock 报告不可确认" : report.status === "confirmed" ? "推送给学生" : report.status === "pushed" ? "已推送" : "确认报告"}</button></div></div>
    </article>`;
  panel.innerHTML = `<article class="card learning-record-card">
    <div class="card-heading"><div><span class="section-kicker">学生原始证据</span><h2>${escapeHTML(result.student.name)}的学习记录</h2><p>${escapeHTML(result.student.class_name)} · 提交时间：${escapeHTML(formatUpdatedAt(result.submitted_at))}</p></div><span class="status success">已提交</span></div>
    <section class="conversation-record">
      <div class="record-section-heading"><div><h3>学生与 AI 对话记录</h3><p>共 ${studentMessages} 次学生发言，保留完整时间顺序。</p></div></div>
      <div class="conversation-messages" role="region" aria-label="${escapeHTML(result.student.name)}的对话记录" tabindex="0">${result.messages.map(message => `<div class="record-message ${message.role === "student" ? "student" : "ai"}"><b>${message.role === "student" ? "学生" : "AI"}</b><p>${escapeHTML(message.content)}</p><time>${escapeHTML(formatUpdatedAt(message.created_at))}</time></div>`).join("")}</div>
    </section>
    <section class="submitted-work"><div class="record-section-heading"><div><h3>最终作答文本</h3><p>${result.submitted_text ? "学生提交的成果原文。" : "学生未填写，可依据对话记录继续分析。"}</p></div></div>${submittedWork}</section>
  </article>${reportCard}`;
}

function renderClassReport(payload) {
  const classrooms = payload.classrooms || [];
  const select = document.getElementById("feedbackClassSelect");
  if (!classrooms.some(item => item.id === ui.activeClassroomId)) {
    ui.activeClassroomId = classrooms[0]?.id || null;
  }
  select.innerHTML = classrooms.map(item =>
    `<option value="${escapeHTML(item.id)}" ${item.id === ui.activeClassroomId ? "selected" : ""}>${escapeHTML(item.name)}</option>`
  ).join("");
  const classroomResults = (payload.results || []).filter(item => item.student.classroom_id === ui.activeClassroomId);
  document.getElementById("feedbackSubmissionStatus").textContent = `${classroomResults.length} 人已提交`;
  const report = payload.class_reports?.[ui.activeClassroomId];
  const panel = document.getElementById("classEvidencePanel");
  if (!classrooms.length || !report) {
    panel.innerHTML = '<article class="card stage-empty-state"><span>等待个体报告</span><h2>尚无班级诊断报告</h2><p>学生提交成果并生成个体报告后，这里会自动汇总更新。</p></article>';
    return;
  }
  const stateText = { queued: "等待生成", running: "正在生成", failed: "生成失败", ready: "AI 初判 · 教师核对" }[report.status] || "等待生成";
  if (!report.result) {
    panel.innerHTML = `<article class="card stage-empty-state"><span>solo-class-diagnosis-intervention</span><h2>${stateText}</h2><p>${report.error ? escapeHTML(report.error) : "基于已有学生个体报告，正在汇总班级思维结构与教学决策依据。"}</p>${report.status === "failed" ? '<button class="button primary" id="retryClassReport">重新生成班级报告</button>' : ""}</article>`;
    return;
  }
  const data = report.result;
  const distribution = data.class_summary.distribution;
  const priorities = data.teaching_priorities;
  const groups = data.grouping_recommendations;
  const studentNames = items => (items || []).map(item => escapeHTML(item.student_name)).join("、") || "无";
  const basisDetails = (title, items) => `<details class="class-basis-details"><summary>${title}</summary><ul>${(items || []).map(item => `<li>${escapeHTML(item)}</li>`).join("")}</ul></details>`;
  const priorityCard = (title, item) => `<article><h3>${title}</h3><p>${escapeHTML(item.statement)}</p>${basisDetails("诊断依据", item.diagnosis_basis)}${basisDetails("目标依据", item.goal_basis)}</article>`;
  const groupCard = (group, type) => `<article><b>${escapeHTML(group.group_id)} · ${studentNames(group.students)}</b><p>${escapeHTML(type === "homogeneous" ? group.common_characteristics : group.grouping_rationale)}</p><small>${escapeHTML(type === "homogeneous" ? group.progression_direction : group.collaboration_direction)}</small></article>`;
  panel.innerHTML = `<article class="card class-report-card">
    <div class="card-heading"><div><span class="section-kicker">班级诊断 · ${data.class_summary.total_students} 份个体报告</span><h2>班级学生思维结构情况</h2></div><span class="status ${report.status === "ready" ? "warning" : "neutral"}">${stateText}</span></div>
    ${report.status === "failed" ? `<p class="class-report-warning">本次自动更新失败：${escapeHTML(report.error || "未知错误")}。下方为上一次报告。</p><button class="button" id="retryClassReport">重新生成</button>` : ""}
    ${["queued", "running"].includes(report.status) ? '<p class="class-report-warning">正在更新报告；下方暂显示上一版本。</p>' : ""}
    <div class="class-distribution">${distribution.map(item => `<section class="solo-step level-${item.level.toLowerCase()}"><header><span>${item.level}</span><b>${escapeHTML(item.level_name)}</b><strong>${item.count} 人 · ${Math.round(item.proportion * 100)}%</strong></header><p>${studentNames(item.students)}</p><small>${escapeHTML(item.level_meaning)}</small></section>`).join("")}</div>
    <p class="solo-note">SOLO 层级只表示学生在本次任务中的思维结构，不是固定能力标签。</p>
  </article>
  <article class="card class-report-card"><h2>整体与分层分析</h2><p>${escapeHTML(data.overall_diagnosis.overall_characteristics)}</p><p>${escapeHTML(data.overall_diagnosis.differentiation_summary)}</p><p><b>主要进阶方向：</b>${escapeHTML(data.overall_diagnosis.main_progression_direction)}</p>
    <div class="class-report-grid">${data.level_analyses.map(item => `<section><h3>${item.level}（${escapeHTML(item.level_name)}）· ${item.count} 人</h3><p>${escapeHTML(item.main_performance)}</p><small>主要障碍：${escapeHTML(item.main_obstacles.join("；"))}</small><small>典型依据：${item.typical_evidence.map(evidence => `${escapeHTML(evidence.student_name)}： “${escapeHTML(evidence.quote)}”`).join("；")}</small></section>`).join("")}</div>
  </article>
  <article class="card class-report-card"><h2>教学重难点</h2><div class="class-report-grid">${priorityCard("教学重点", priorities.teaching_focus)}${priorityCard("教学难点", priorities.teaching_difficulty)}</div></article>
  <article class="card class-report-card"><h2>临时分组建议</h2><p>${escapeHTML(groups.usage_note)}</p><div class="class-report-grid"><section><h3>同质分组</h3>${groups.homogeneous_groups.length ? groups.homogeneous_groups.map(item => groupCard(item, "homogeneous")).join("") : "<p>当前不建议同质分组。</p>"}</section><section><h3>异质分组</h3>${groups.heterogeneous_groups.length ? groups.heterogeneous_groups.map(item => groupCard(item, "heterogeneous")).join("") : `<p>${escapeHTML(groups.heterogeneous_not_recommended_reason || "当前不建议异质分组。")}</p>`}</section></div><p>未分组学生：${studentNames(groups.ungrouped_students)}</p></article>
  <article class="card class-report-card"><h2>后续干预设计依据</h2><div class="class-report-grid">${data.intervention_design_basis.map(item => `<section><h3>${escapeHTML(item.basis_id)} · ${studentNames(item.target_students)}</h3><p>${escapeHTML(item.diagnostic_finding)}</p><p><b>认知变化：</b>${escapeHTML(item.intended_cognitive_change)}</p><small>设计条件：${escapeHTML(item.design_requirements.join("；"))}</small></section>`).join("")}</div><details><summary>查看完整教师报告</summary><pre class="class-report-markdown">${escapeHTML(report.report_text || "")}</pre></details></article>`;
}

function renderStudentResults(payload) {
  renderClassReport(payload);
  const results = (payload.results || []).filter(item => item.student.classroom_id === ui.activeClassroomId);
  const generatedCount = results.filter(item => item.report).length;
  const confirmedCount = results.filter(item => ["confirmed", "pushed"].includes(item.report?.status)).length;
  const pushableCount = results.filter(item => item.report?.status === "confirmed" && !item.report._saving && !item.report._saveError && item.report.provider !== "mock").length;
  document.getElementById("analysisReviewStatus").textContent = results.length ? `${confirmedCount}/${results.length} 已确认` : "等待学生报告";
  document.getElementById("feedbackProgressStatus").textContent = results.length ? `${confirmedCount}/${results.length} 已确认` : "等待教师确认";
  const studentPanel = document.getElementById("studentEvidencePanel");
  if (!results.length) {
    studentPanel.innerHTML = '<article class="card stage-empty-state"><span>暂无提交</span><h2>还没有学生学习记录</h2><p>学生完成 AI 对话并提交文字成果后，可在这里生成个体报告。</p></article>';
    return;
  }
  ui.activeStudentResultIndex = Math.min(ui.activeStudentResultIndex, results.length - 1);
  const rubricReady = serverState.current_workspace?.rubric?.status === "confirmed";
  studentPanel.innerHTML = `<div class="card student-feedback-toolbar"><div><b>个体报告生成</b><span>${generatedCount}/${results.length} 份已生成 · ${pushableCount} 份已确认待推送</span></div><div class="student-feedback-actions"><button class="button" id="generateAllStudentReports" ${rubricReady ? "" : "disabled"}>${rubricReady ? "生成全部待分析报告" : "请先确认任务量规"}</button><button class="button primary" id="pushConfirmedStudentReports" ${pushableCount ? "" : "disabled"}>一键推送已确认报告</button></div></div><div class="student-review-layout">
    <aside class="card student-list" id="studentResultList"><div class="list-header"><b>${results.length} 名学生</b><span>真实提交</span></div>${results.map((result, index) => `<button class="${index === ui.activeStudentResultIndex ? "active" : ""}" data-result-index="${index}"><span><b>${escapeHTML(result.student.name)}</b><small>${result.messages.filter(message => message.role === "student").length} 次发言 · ${result.submitted_text ? "有文字成果" : "仅有会话"}</small></span><em>${result.report ? ({ draft: "待确认", confirmed: "已确认", pushed: "已推送", stale: "需重生成" }[result.report.status]) : "待生成"}</em></button>`).join("")}</aside>
    <div class="student-review-main" id="studentResultDetail"></div>
  </div>`;
  renderStudentResultDetail(results[ui.activeStudentResultIndex]);
  document.querySelectorAll("[data-result-index]").forEach(button => button.addEventListener("click", () => {
    document.querySelectorAll("[data-result-index]").forEach(item => item.classList.toggle("active", item === button));
    ui.activeStudentResultIndex = Number(button.dataset.resultIndex);
    renderStudentResultDetail(results[Number(button.dataset.resultIndex)]);
  }));
}

async function loadStudentResults(teachingId) {
  try {
    const payload = await window.PlatformAPI.getStudentResults(teachingId);
    if (serverState?.current_teaching_id !== teachingId) return;
    serverState.studentResults = payload;
    renderStudentResults(payload);
    const classSelect = document.getElementById("interventionClassSelect");
    const classrooms = payload.classrooms || [];
    if (ui.interventionClassroomTeachingId !== teachingId || !classrooms.some(item => item.id === ui.activeInterventionClassroomId)) {
      ui.interventionClassroomTeachingId = teachingId;
      ui.activeInterventionClassroomId = classrooms.find(item => payload.class_reports?.[item.id]?.status === "ready")?.id || classrooms[0]?.id || null;
      serverState.activityFormative = null;
      ui.activityFormativeError = null;
    }
    classSelect.innerHTML = classrooms.map(item =>
      `<option value="${escapeHTML(item.id)}" ${item.id === ui.activeInterventionClassroomId ? "selected" : ""}>${escapeHTML(item.name)}</option>`
    ).join("");
    if (ui.currentPage === "intervention") loadGoalPath(teachingId, ui.activeInterventionClassroomId);
    if (ui.currentPage === "intervention" && ui.interventionStep === 3) loadActivityFormative(teachingId, ui.activeInterventionClassroomId);
  } catch (error) {
    console.error("Failed to load student results", error);
  }
}

function goalPathTeacherInput() {
  return {
    teacher_instructional_context: {
      teaching_content_and_curriculum_analysis: document.getElementById("teachingContentAnalysis").value.trim(),
      teaching_focus_and_difficulty_analysis: document.getElementById("teachingFocusAnalysis").value.trim(),
      planned_duration_minutes: Number(document.getElementById("interventionDurationMinutes").value),
      teaching_environment_and_ai_support_conditions: document.getElementById("teachingEnvironmentConditions").value.trim(),
      teacher_lesson_conception: document.getElementById("teacherLessonConception").value.trim() || null
    },
    // Keep older running servers compatible until they are restarted.
    content_analysis_confirmed: true,
    focus_analysis_confirmed: true
  };
}

function goalPathReadOnlyRows(path) {
  return path.stages.map(stage => stage.activity_units.map((unit, unitIndex) => `<tr>
    ${unitIndex === 0 ? `<td rowspan="${stage.activity_units.length}"><span class="goal-path-stage-code">${escapeHTML(stage.stage_id)}</span>${escapeHTML(stage.stage_name)}</td>` : ""}
    <td>${escapeHTML(unit.activity_name)}</td>
    <td>${escapeHTML(unit.organization_name)}</td>
    <td>${escapeHTML(unit.activity_summary)}</td>
    <td>${escapeHTML(unit.target_students.map(student => student.student_name).join("、") || "—")}<small>${escapeHTML(unit.target_goal_ids.join("、") || "—")}</small></td>
    <td>${unitIndex === 0 ? `${Number(stage.duration_minutes)} 分钟` : "同阶段"}</td>
  </tr>`).join("")).join("");
}

function renderGoalPath(payload) {
  const design = payload?.design;
  const available = Boolean(payload?.class_diagnosis_ready);
  const status = document.getElementById("goalPathStatus");
  const prerequisite = document.getElementById("goalPathPrerequisite");
  const content = document.getElementById("goalPathGeneratedContent");
  const generate = document.getElementById("generateGoalPath");
  const confirm = document.getElementById("confirmGoalPath");
  const saveDraft = document.getElementById("saveGoalPathDraft");
  const next = document.getElementById("nextFromGoalPath");
  const legacyCompleted = !design && serverState?.current_workspace?.intervention?.status === "completed";
  generate.disabled = !available;
  generate.hidden = !design;
  document.getElementById("generateTeacherAnalysis").disabled = !available;
  confirm.hidden = !design || design.status === "stale";
  saveDraft.hidden = !design || design.status === "stale";
  next.disabled = design?.status !== "teacher_confirmed";
  const activityTab = document.querySelector('#interventionSteps [data-istep="3"]');
  activityTab.disabled = design?.status !== "teacher_confirmed" && !legacyCompleted;
  activityTab.title = activityTab.disabled ? "请先生成并确认目标与路径" : "";
  const resourceTab = document.querySelector('#interventionSteps [data-istep="4"]');
  resourceTab.disabled = serverState?.activityFormative?.design?.status !== "teacher_confirmed" && !legacyCompleted;
  resourceTab.title = resourceTab.disabled ? "请先确认学习活动与形成性评价" : "";
  status.textContent = !available ? "等待班级报告" : design?.status === "stale" ? "上游诊断已更新" :
    design?.status === "teacher_confirmed" ? "教师已确认" : design ? "待教师确认" : "待生成";
  status.className = `status ${design?.status === "teacher_confirmed" ? "success" : available ? "warning" : "neutral"}`;
  prerequisite.innerHTML = !available
    ? '<p class="goal-path-notice">当前班级尚无可用的 SOLO 聚合诊断报告。请先在“诊断结果反馈”生成学生个体报告与班级报告，再回到这里。</p>'
    : design?.status === "stale"
      ? '<p class="goal-path-notice">班级诊断已更新。请核对第 1 步的教学条件后重新生成，旧方案仅供对照。</p>'
      : '';
  if (!design) {
    content.innerHTML = `<div class="stage-empty-state"><span>目标与路径</span><h2>尚未生成</h2><button class="button primary" id="generateGoalPathEmpty" ${available ? "" : "disabled"}>AI生成目标与路径</button></div>`;
    return;
  }
  const result = design.result;
  const common = result.common_core_goal;
  const path = result.intervention_path;
  const students = list => (list || []).map(item => escapeHTML(item.student_name)).join("、");
  const organizationOptions = [
    ["C", "全班共同活动（C）"], ["H", "同质小组活动（H）"],
    ["X", "异质小组协同（X）"], ["I", "个体学习或个别支持（I）"],
    ["S", "学习站轮转（S）"], ["A", "综合应用（A）"]
  ];
  const activityRow = (stage, stageIndex, unit, unitIndex) => {
    const targetStudents = new Set(unit.target_students.map(student => student.student_id));
    const targetGoals = new Set(unit.target_goal_ids);
    const picker = `<details class="goal-path-target-picker"><summary>${students(unit.target_students) || "选择学生"}<small>${escapeHTML(unit.target_goal_ids.join("、") || "选择目标")}</small></summary>
      <div><strong>面向学生</strong>${result.student_goal_assignments.map(student => `<label><input type="checkbox" data-gp-target-student data-gp-stage="${stageIndex}" data-gp-unit="${unitIndex}" value="${escapeHTML(student.student_id)}" ${targetStudents.has(student.student_id) ? "checked" : ""}>${escapeHTML(student.student_name)}</label>`).join("")}</div>
      <div><strong>目标</strong>${result.progression_goals.map(goal => `<label><input type="checkbox" data-gp-target-goal data-gp-stage="${stageIndex}" data-gp-unit="${unitIndex}" value="${escapeHTML(goal.goal_id)}" ${targetGoals.has(goal.goal_id) ? "checked" : ""}>${escapeHTML(goal.goal_id)} · ${escapeHTML(goal.target_level_name)}</label>`).join("")}</div></details>`;
    return `<tr>${unitIndex === 0 ? `<td rowspan="${stage.activity_units.length}"><span class="goal-path-stage-code">${escapeHTML(stage.stage_id)}</span><textarea aria-label="${escapeHTML(stage.stage_id)} 阶段名称" data-gp-stage-name="${stageIndex}" rows="2">${escapeHTML(stage.stage_name)}</textarea></td>` : ""}
      <td><textarea data-gp-stage="${stageIndex}" data-gp-unit="${unitIndex}" data-gp-unit-field="activity_name" rows="2">${escapeHTML(unit.activity_name)}</textarea></td>
      <td><select aria-label="${escapeHTML(stage.stage_id)} 活动组织形式" data-gp-org-code data-gp-stage="${stageIndex}" data-gp-unit="${unitIndex}">${organizationOptions.map(([code, label]) => `<option value="${code}" ${code === unit.organization_code ? "selected" : ""}>${escapeHTML(code === unit.organization_code ? unit.organization_name : label)}</option>`).join("")}</select></td>
      <td><textarea data-gp-stage="${stageIndex}" data-gp-unit="${unitIndex}" data-gp-unit-field="activity_summary" rows="3">${escapeHTML(unit.activity_summary)}</textarea></td>
      <td>${picker}</td><td>${unitIndex === 0 ? `<input type="number" min="1" data-gp-stage-duration="${stageIndex}" value="${stage.duration_minutes}">分钟` : "同阶段"}</td></tr>`;
  };
  const activityHead = "<thead><tr><th>阶段</th><th>活动名称</th><th>组织形式</th><th>活动内容简介</th><th>面向学生与目标</th><th>建议时长</th></tr></thead>";
  content.innerHTML = `<div class="goal-path-generated">
    <section><h3>共同核心目标</h3><label>核心目标<textarea data-gp-common="goal_statement" rows="3">${escapeHTML(common.goal_statement)}</textarea></label>
      <label>目标认知结构及理由 <small>${escapeHTML(common.target_cognitive_structure.solo_level)}（${escapeHTML(common.target_cognitive_structure.level_name)}）</small><textarea data-gp-structure rows="3">${escapeHTML(common.target_cognitive_structure.structure_and_rationale)}</textarea></label>
      <label>可观察的达成表现（每行一条）<textarea data-gp-criteria rows="3">${escapeHTML(common.observable_success_criteria.join("\n"))}</textarea></label></section>
    <section><h3>分层进阶目标</h3><div class="goal-path-table-wrap"><table class="goal-path-goals-table"><thead><tr><th>目标层级</th><th>具体目标内容</th><th>相关学生姓名</th><th>可观察的达成表现</th></tr></thead><tbody>${result.progression_goals.map((goal, index) => `<tr><td>${escapeHTML(goal.target_level_name)}<small>面向 ${escapeHTML(goal.source_levels.join("、"))}</small></td><td><textarea data-gp-goal="${index}" data-gp-field="goal_statement" rows="3">${escapeHTML(goal.goal_statement)}</textarea></td><td>${students(goal.target_students)}</td><td><textarea data-gp-goal="${index}" data-gp-field="observable_achievement" rows="3">${escapeHTML(goal.observable_achievement)}</textarea></td></tr>`).join("")}</tbody></table></div><p class="goal-path-help">学生归属来自上游诊断；若需调整名单，请修改或补充个体诊断后重新生成。</p></section>
    <section><h3>推荐课堂组织与推进</h3><p><b>主要组织模式：</b>${escapeHTML(path.primary_path_label)}</p><p><b>模式说明：</b>${escapeHTML(path.mode_explanation)}</p><ul>${path.recommendation_reasons.map(reason => `<li>${escapeHTML(reason)}</li>`).join("")}</ul><p><b>课堂推进安排：</b>${escapeHTML(path.teacher_facing_path)}</p>${path.alternative_path ? `<p><b>备选：</b>${escapeHTML(path.alternative_path.path_label)}；${escapeHTML(path.alternative_path.suitable_when)}；代价：${escapeHTML(path.alternative_path.tradeoff)}</p>` : ""}</section>
    <section><div class="goal-path-path-heading"><h3>课堂活动路径</h3><button type="button" class="button small" data-gp-toggle-edit>修改</button></div><p>同一阶段内的活动同时开展，时长只计算一次。</p>
      <div class="goal-path-table-wrap" data-gp-path-readonly><table class="goal-path-activity-table">${activityHead}<tbody>${goalPathReadOnlyRows(path)}</tbody></table></div>
      <div class="goal-path-table-wrap" data-gp-path-editor hidden><table class="goal-path-activity-table goal-path-activity-editor">${activityHead}<tbody>${path.stages.map((stage, stageIndex) => stage.activity_units.map((unit, unitIndex) => activityRow(stage, stageIndex, unit, unitIndex)).join("")).join("")}</tbody></table></div>
      <p class="goal-path-help">阶段总时长：${path.total_duration_minutes} 分钟；请保持与第 1 步设定一致。修改后请点击下方“保存修改”或“确认目标与路径”。</p></section>
    <section><h3>请教师确认</h3><ul>${result.teacher_confirmation.items_to_confirm.map(item => `<li>${escapeHTML(item)}</li>`).join("")}</ul>${result.teacher_confirmation.unresolved_decisions.length ? `<p>待决定：${escapeHTML(result.teacher_confirmation.unresolved_decisions.join("；"))}</p>` : ""}</section>
  </div>`;
}

function activityRequirementsInput() {
  return {
    ai_access: "unspecified",
    teacher_notes: [],
    fixed_learning_materials: [], required_activities: [], prohibited_approaches: [], ai_use_constraints: []
  };
}

function renderNumberedActivityLines(value) {
  const lines = String(value || "").split(/\r?\n/)
    .map(line => line.replace(/^\s*\d+[.、)]\s*/, "").trim()).filter(Boolean);
  return lines.length
    ? `<ol class="af-action-list">${lines.map(line => `<li>${escapeHTML(line)}</li>`).join("")}</ol>`
    : '<span class="af-muted">暂无内容</span>';
}

function renderActivityFormative(payload) {
  ui.activityFormativeDirty = false;
  const design = payload?.design;
  const ready = Boolean(payload?.goal_path_ready);
  const legacyCompleted = !design && serverState?.current_workspace?.intervention?.status === "completed";
  document.getElementById("activityFormativeSkillPanel").hidden = legacyCompleted;
  document.getElementById("legacyActivityEditor").hidden = !legacyCompleted;
  const status = document.getElementById("activityFormativeStatus");
  const prerequisite = document.getElementById("activityFormativePrerequisite");
  const content = document.getElementById("activityFormativeGeneratedContent");
  const errorNotice = document.getElementById("activityFormativeError");
  errorNotice.hidden = !ui.activityFormativeError;
  errorNotice.textContent = ui.activityFormativeError || "";
  const generate = document.getElementById("generateActivityFormative");
  generate.disabled = !ready;
  generate.hidden = !design;
  generate.textContent = "AI重新生成学习活动";
  document.getElementById("saveActivityFormativeDraft").hidden = !design || design.status === "stale";
  document.getElementById("confirmActivityFormative").hidden = !design || design.status === "stale";
  document.getElementById("nextFromActivityFormative").disabled = design?.status !== "teacher_confirmed" && !legacyCompleted;
  const resourceTab = document.querySelector('#interventionSteps [data-istep="4"]');
  resourceTab.disabled = design?.status !== "teacher_confirmed" && !legacyCompleted;
  resourceTab.title = resourceTab.disabled ? "请先确认学习活动与形成性评价" : "";
  status.textContent = ui.activityFormativeError ? "生成失败" : !ready ? "等待上游目标路径" : design?.status === "stale" ? "上游目标路径已更新" :
    design?.status === "teacher_confirmed" ? "教师已确认" : design ? "待教师确认" : "待生成";
  status.className = `status ${design?.status === "teacher_confirmed" ? "success" : ready ? "warning" : "neutral"}`;
  prerequisite.textContent = !ready
    ? "请先在第 2 步确认当前班级的目标与活动路径。已完成的旧教案仍可查看，但不会作为新 Skill 的输入。"
    : design?.status === "stale"
      ? "上游目标与路径已修改，请重新生成学习活动；旧方案仅供对照。"
      : "";
  prerequisite.hidden = ready && design?.status !== "stale";
  prerequisite.className = "goal-path-notice";
  if (!design) {
    content.innerHTML = ui.activityFormativeError
      ? `<div class="stage-empty-state"><span>学习活动与形成性评价</span><h2>生成失败</h2><button class="button primary" id="generateActivityFormativeEmpty" ${ready ? "" : "disabled"}>重新生成学习活动</button></div>`
      : `<div class="stage-empty-state"><span>学习活动与形成性评价</span><h2>尚未生成</h2><button class="button primary" id="generateActivityFormativeEmpty" ${ready ? "" : "disabled"}>AI生成学习活动</button></div>`;
    return;
  }
  const result = design.result;
  const presentationOverrides = design.presentation_overrides || {};
  const goalNames = new Map([["CG", "共同核心目标"], ...(serverState?.goalPath?.design?.result?.progression_goals || []).map(item => [item.goal_id, item.target_level_name])]);
  const goalLabel = ids => ids.map(id => goalNames.get(id) || id).join("、");
  const actionsFor = (activity, actors) => activity.participant_actions.filter(action => actors.includes(action.actor));
  const inline = (value, selector, materials = false) => `<span class="af-inline-text" data-af-inline-target="${escapeHTML(selector)}"${materials ? ' data-af-inline-materials="true"' : ""}>${escapeHTML(value)}</span>`;
  const actionList = (activity, activityIndex, actions) => actions.length
    ? `<ol class="af-action-list">${actions.map(action => `<li>${action.actor === "同伴" ? "同伴：" : ""}${inline(action.action, `[data-af-action='${activityIndex}:${activity.participant_actions.indexOf(action)}']`)}</li>`).join("")}</ol>`
    : '<span class="af-muted">无需单独安排</span>';
  const activityCards = result.activities.map((activity, activityIndex) => {
    const groups = activity.parallel_group_tasks;
    const override = presentationOverrides[activity.activity_id] || {};
    const groupObjectives = override.objective || groups.every(group => group.activity_objective === activity.activity_objective) ? "" : groups.map((group, groupIndex) => `<div class="af-group-line"><b>${escapeHTML(group.group_name)}</b>${inline(group.activity_objective, `[data-af-group='${activityIndex}:${groupIndex}'][data-af-group-field='activity_objective']`)}</div>`).join("");
    const studentActions = actionsFor(activity, ["学生", "同伴"]);
    const groupTasks = override.student || groups.every(group => studentActions.length === 1 && group.student_task === studentActions[0].action) ? "" : groups.map((group, groupIndex) => `<div class="af-group-line"><b>${escapeHTML(group.group_name)}</b>${inline(group.student_task, `[data-af-group='${activityIndex}:${groupIndex}'][data-af-group-field='student_task']`)}</div>`).join("");
    const groupProducts = override.product || groups.every(group => group.learning_product === activity.learning_product) ? "" : groups.map((group, groupIndex) => `<div class="af-group-line"><b>${escapeHTML(group.group_name)}</b>${inline(group.learning_product, `[data-af-group='${activityIndex}:${groupIndex}'][data-af-group-field='learning_product']`)}</div>`).join("");
    const supportCount = activity.scaffold_support.length + groups.reduce((count, group) => count + group.scaffold_support.length, 0);
    const supportSummary = supportCount ? `<details class="af-support-details"><summary>查看差异化支持（${supportCount}项）</summary><ul>${activity.scaffold_support.map((item, supportIndex) => `<li><b>${escapeHTML(item.target_students_or_group)}：</b>${inline(item.support, `[data-af-support='${activityIndex}:${supportIndex}'][data-af-support-field='support']`)} <small>撤除：${inline(item.fading_condition || "", `[data-af-support='${activityIndex}:${supportIndex}'][data-af-support-field='fading_condition']`)}</small></li>`).join("")}${groups.flatMap((group, groupIndex) => group.scaffold_support.map((item, supportIndex) => `<li><b>${escapeHTML(group.group_name)}：</b>${inline(item.support, `[data-af-group-support='${activityIndex}:${groupIndex}:${supportIndex}'][data-af-group-support-field='support']`)} <small>撤除：${inline(item.fading_condition || "", `[data-af-group-support='${activityIndex}:${groupIndex}:${supportIndex}'][data-af-group-support-field='fading_condition']`)}</small></li>`)).join("")}</ul></details>` : "";
    const actionEditors = activity.participant_actions.map((action, actionIndex) => `<label>${escapeHTML(action.actor)}活动<textarea data-af-action="${activityIndex}:${actionIndex}" rows="2">${escapeHTML(action.action)}</textarea></label>`).join("");
    const supportEditors = activity.scaffold_support.map((item, supportIndex) => `<label>${escapeHTML(item.target_students_or_group)} · ${escapeHTML(item.intensity)}支持<textarea data-af-support="${activityIndex}:${supportIndex}" data-af-support-field="support" rows="2">${escapeHTML(item.support)}</textarea></label><label>撤除条件<textarea data-af-support="${activityIndex}:${supportIndex}" data-af-support-field="fading_condition" rows="2">${escapeHTML(item.fading_condition || "")}</textarea></label>`).join("");
    const groupEditors = groups.map((group, groupIndex) => `<section class="af-group-editor"><h5>${escapeHTML(group.group_name)}</h5><label>本组目标<textarea data-af-group="${activityIndex}:${groupIndex}" data-af-group-field="activity_objective" rows="2">${escapeHTML(group.activity_objective)}</textarea></label><label>本组任务<textarea data-af-group="${activityIndex}:${groupIndex}" data-af-group-field="student_task" rows="2">${escapeHTML(group.student_task)}</textarea></label><label>本组产出<textarea data-af-group="${activityIndex}:${groupIndex}" data-af-group-field="learning_product" rows="2">${escapeHTML(group.learning_product)}</textarea></label>${group.scaffold_support.map((item, supportIndex) => `<label>本组支持 · ${escapeHTML(item.intensity)}<textarea data-af-group-support="${activityIndex}:${groupIndex}:${supportIndex}" data-af-group-support-field="support" rows="2">${escapeHTML(item.support)}</textarea></label><label>撤除条件<textarea data-af-group-support="${activityIndex}:${groupIndex}:${supportIndex}" data-af-group-support-field="fading_condition" rows="2">${escapeHTML(item.fading_condition || "")}</textarea></label>`).join("")}</section>`).join("");
    return `<article class="activity-lesson-card"><header><div class="af-activity-title"><small>活动 ${activityIndex + 1} · ${activity.duration_minutes} 分钟</small><h4>${escapeHTML(activity.activity_name)}</h4><p>${escapeHTML(activity.organization_forms.join(" · "))}</p></div>${groups.length ? `<span class="af-parallel-badge">${groups.length} 个并行单元</span>` : ""}</header>
      <table class="activity-lesson-table"><tbody><tr><th>活动目标</th><td class="af-edit-block" data-af-edit-label="活动目标" data-af-unified="objective" data-af-activity-index="${activityIndex}"><p>${inline(override.objective || activity.activity_objective, `[data-af-presentation='${activityIndex}:objective']`)}</p>${groupObjectives ? `<div class="af-group-summary">${groupObjectives}</div>` : ""}</td></tr><tr><th>活动过程</th><td><div class="activity-process-grid"><div class="af-edit-block" data-af-edit-label="教师活动"><b>教师活动</b>${actionList(activity, activityIndex, actionsFor(activity, ["教师"]))}</div><div class="af-edit-block" data-af-edit-label="学生活动" data-af-unified="student" data-af-activity-index="${activityIndex}"><b>学生活动</b>${override.student ? renderNumberedActivityLines(override.student) : actionList(activity, activityIndex, studentActions)}${groupTasks ? `<div class="af-group-summary">${groupTasks}</div>` : ""}</div><div class="af-edit-block" data-af-edit-label="AI辅助"><b>AI辅助</b>${actionList(activity, activityIndex, actionsFor(activity, ["AI"]))}</div></div></td></tr><tr><th>学习材料与资源</th><td class="af-edit-block" data-af-edit-label="学习材料与资源"><p>${inline(activity.learning_materials.join("、"), `[data-af-materials='${activityIndex}']`, true)}</p></td></tr><tr><th>学习产出</th><td class="af-edit-block" data-af-edit-label="学习产出" data-af-unified="product" data-af-activity-index="${activityIndex}"><p>${inline(override.product || activity.learning_product, `[data-af-presentation='${activityIndex}:product']`)}</p>${groupProducts ? `<div class="af-group-summary">${groupProducts}</div>` : ""}</td></tr></tbody></table>
      ${supportSummary}<div class="af-hidden-editors" hidden><label>活动名称<input data-af-activity="${activityIndex}" data-af-field="activity_name" value="${escapeHTML(activity.activity_name)}"></label><label>活动目标<textarea data-af-activity="${activityIndex}" data-af-field="activity_objective">${escapeHTML(activity.activity_objective)}</textarea></label>${actionEditors}<label>学习材料与资源<textarea data-af-materials="${activityIndex}">${escapeHTML(activity.learning_materials.join("\n"))}</textarea></label><label>学习产出<textarea data-af-activity="${activityIndex}" data-af-field="learning_product">${escapeHTML(activity.learning_product)}</textarea></label>${supportEditors}${groupEditors}<input data-af-new-action="${activityIndex}:AI" value=""><input data-af-presentation="${activityIndex}:objective" value="${escapeHTML(override.objective || "")}"><input data-af-presentation="${activityIndex}:student" value="${escapeHTML(override.student || "")}"><input data-af-presentation="${activityIndex}:product" value="${escapeHTML(override.product || "")}"></div></article>`;
  }).join("");
  const evaluationRows = result.formative_evaluation_nodes.map((evaluation, evaluationIndex) => {
    const activityNumber = result.activities.findIndex(item => item.activity_id === evaluation.after_activity_ids.at(-1)) + 1;
    const criteria = evaluation.judgment_criteria.map((item, criterionIndex) => `<p><b>${escapeHTML(goalNames.get(item.goal_id) || item.goal_id)}：</b>${inline(item.meets_when, `[data-af-criterion='${evaluationIndex}:${criterionIndex}'][data-af-criterion-field='meets_when']`)}</p><small>尚未达到：${inline(item.not_yet_when, `[data-af-criterion='${evaluationIndex}:${criterionIndex}'][data-af-criterion-field='not_yet_when']`)}</small>`).join("");
    const rules = evaluation.adjustment_rules;
    return `<tr><td><b>活动${activityNumber}之后</b></td><td class="af-edit-block" data-af-edit-label="对象与目标"><p>${inline(evaluation.evaluation_purpose, `[data-af-eval='${evaluationIndex}'][data-af-eval-field='evaluation_purpose']`)}</p><small>${escapeHTML(goalLabel(evaluation.target_goal_ids))}</small></td><td class="af-edit-block" data-af-edit-label="评价任务与证据"><p>${inline(evaluation.evidence_task, `[data-af-eval='${evaluationIndex}'][data-af-eval-field='evidence_task']`)}</p><small>${inline(evaluation.evidence_description, `[data-af-eval='${evaluationIndex}'][data-af-eval-field='evidence_description']`)}</small></td><td class="af-edit-block" data-af-edit-label="达成标准">${criteria}</td><td class="af-edit-block" data-af-edit-label="评价结果处理"><p><b>达到：</b>${inline(rules.if_goal_reached, `[data-af-rule='${evaluationIndex}'][data-af-rule-field='if_goal_reached']`)}</p><p><b>尚未达到：</b>${inline(rules.if_not_yet, `[data-af-rule='${evaluationIndex}'][data-af-rule-field='if_not_yet']`)}</p><p><b>证据不清：</b>${inline(rules.if_evidence_not_individual, `[data-af-rule='${evaluationIndex}'][data-af-rule-field='if_evidence_not_individual']`)}</p></td><td class="af-edit-block" data-af-edit-label="判断主体">${inline(evaluation.decision_by, `[data-af-decision='${evaluationIndex}']`)}</td></tr>`;
  }).join("");
  const evaluationEditors = result.formative_evaluation_nodes.map((evaluation, evaluationIndex) => `<section class="af-evaluation-editor"><h4>评价 ${evaluationIndex + 1}</h4><div class="af-editor-grid"><label>对象与目标<textarea data-af-eval="${evaluationIndex}" data-af-eval-field="evaluation_purpose" rows="2">${escapeHTML(evaluation.evaluation_purpose)}</textarea></label><label>评价任务<textarea data-af-eval="${evaluationIndex}" data-af-eval-field="evidence_task" rows="2">${escapeHTML(evaluation.evidence_task)}</textarea></label><label>证据<textarea data-af-eval="${evaluationIndex}" data-af-eval-field="evidence_description" rows="2">${escapeHTML(evaluation.evidence_description)}</textarea></label>${evaluation.judgment_criteria.map((criterion, criterionIndex) => `<label>${escapeHTML(goalNames.get(criterion.goal_id) || criterion.goal_id)} · 达到<textarea data-af-criterion="${evaluationIndex}:${criterionIndex}" data-af-criterion-field="meets_when" rows="2">${escapeHTML(criterion.meets_when)}</textarea></label><label>尚未达到<textarea data-af-criterion="${evaluationIndex}:${criterionIndex}" data-af-criterion-field="not_yet_when" rows="2">${escapeHTML(criterion.not_yet_when)}</textarea></label>`).join("")}<label>达到后的处理<textarea data-af-rule="${evaluationIndex}" data-af-rule-field="if_goal_reached" rows="2">${escapeHTML(evaluation.adjustment_rules.if_goal_reached)}</textarea></label><label>尚未达到的处理<textarea data-af-rule="${evaluationIndex}" data-af-rule-field="if_not_yet" rows="2">${escapeHTML(evaluation.adjustment_rules.if_not_yet)}</textarea></label><label>个人证据不清时<textarea data-af-rule="${evaluationIndex}" data-af-rule-field="if_evidence_not_individual" rows="2">${escapeHTML(evaluation.adjustment_rules.if_evidence_not_individual)}</textarea></label><label>判断主体<select data-af-decision="${evaluationIndex}">${["教师判断", "AI汇总后教师判断", "教师结合课堂观察与AI汇总判断"].map(option => `<option ${option === evaluation.decision_by ? "selected" : ""}>${option}</option>`).join("")}</select></label></div></section>`).join("");
  content.innerHTML = `<div class="activity-formative-generated"><h3>一、学习活动设计</h3>${activityCards}<h3>二、形成性评价</h3><div class="formative-table-wrap"><table class="formative-table"><thead><tr><th>评价时机</th><th>对象与目标</th><th>评价任务与证据</th><th>达成标准</th><th>评价结果处理</th><th>判断主体</th></tr></thead><tbody>${evaluationRows}</tbody></table></div><div class="af-hidden-editors" hidden>${evaluationEditors}</div><section class="activity-confirm-list"><h3>三、请教师确认</h3><ul>${result.teacher_confirmation.items_to_confirm.map(item => `<li>${escapeHTML(item)}</li>`).join("")}</ul>${result.teacher_confirmation.unresolved_questions.length ? `<p>待决定：${escapeHTML(result.teacher_confirmation.unresolved_questions.join("；"))}</p>` : ""}</section></div>`;
  content.querySelectorAll("input[data-af-presentation]").forEach(input => {
    const [index, key] = input.dataset.afPresentation.split(":");
    const field = document.createElement("textarea");
    field.dataset.afPresentation = input.dataset.afPresentation;
    field.value = presentationOverrides[result.activities[Number(index)]?.activity_id]?.[key] || "";
    input.replaceWith(field);
  });
}

async function loadActivityFormative(teachingId, classroomId) {
  if (!teachingId || !classroomId) return;
  try {
    const payload = await window.PlatformAPI.getActivityFormative(teachingId, classroomId);
    if (serverState?.current_teaching_id !== teachingId || ui.activeInterventionClassroomId !== classroomId) return;
    serverState.activityFormative = payload;
    renderActivityFormative(payload);
  } catch (error) {
    const prerequisite = document.getElementById("activityFormativePrerequisite");
    prerequisite.hidden = false;
    prerequisite.textContent = `无法读取学习活动方案：${error.message}`;
  }
}

function collectActivityFormativeEdits() {
  const original = serverState?.activityFormative?.design?.result;
  if (!original) return null;
  const result = structuredClone(original);
  const root = document.getElementById("activityFormativeGeneratedContent");
  root.querySelectorAll("[data-af-activity]").forEach(field => {
    result.activities[Number(field.dataset.afActivity)][field.dataset.afField] = field.value.trim();
  });
  root.querySelectorAll("[data-af-materials]").forEach(field => {
    result.activities[Number(field.dataset.afMaterials)].learning_materials = field.value.split("\n").map(item => item.trim()).filter(Boolean);
  });
  root.querySelectorAll("[data-af-action]").forEach(field => {
    const [activityIndex, actionIndex] = field.dataset.afAction.split(":").map(Number);
    result.activities[activityIndex].participant_actions[actionIndex].action = field.value.trim();
  });
  root.querySelectorAll("[data-af-new-action]").forEach(field => {
    if (!field.value.trim()) return;
    const [activityIndex, actor] = field.dataset.afNewAction.split(":");
    result.activities[Number(activityIndex)].participant_actions.push({actor, action: field.value.trim(), order: 0});
  });
  result.activities.forEach(activity => {
    activity.participant_actions = activity.participant_actions.filter(action => action.action);
    activity.participant_actions.forEach((action, index) => { action.order = index + 1; });
  });
  root.querySelectorAll("[data-af-support]").forEach(field => {
    const [activityIndex, supportIndex] = field.dataset.afSupport.split(":").map(Number);
    result.activities[activityIndex].scaffold_support[supportIndex][field.dataset.afSupportField] = field.value.trim() || null;
  });
  root.querySelectorAll("[data-af-group]").forEach(field => {
    const [activityIndex, groupIndex] = field.dataset.afGroup.split(":").map(Number);
    result.activities[activityIndex].parallel_group_tasks[groupIndex][field.dataset.afGroupField] = field.value.trim();
  });
  root.querySelectorAll("[data-af-group-support]").forEach(field => {
    const [activityIndex, groupIndex, supportIndex] = field.dataset.afGroupSupport.split(":").map(Number);
    result.activities[activityIndex].parallel_group_tasks[groupIndex].scaffold_support[supportIndex][field.dataset.afGroupSupportField] = field.value.trim() || null;
  });
  root.querySelectorAll("[data-af-eval]").forEach(field => {
    result.formative_evaluation_nodes[Number(field.dataset.afEval)][field.dataset.afEvalField] = field.value.trim();
  });
  root.querySelectorAll("[data-af-rule]").forEach(field => {
    result.formative_evaluation_nodes[Number(field.dataset.afRule)].adjustment_rules[field.dataset.afRuleField] = field.value.trim();
  });
  root.querySelectorAll("[data-af-criterion]").forEach(field => {
    const [evaluationIndex, criterionIndex] = field.dataset.afCriterion.split(":").map(Number);
    result.formative_evaluation_nodes[evaluationIndex].judgment_criteria[criterionIndex][field.dataset.afCriterionField] = field.value.trim();
  });
  root.querySelectorAll("[data-af-decision]").forEach(field => {
    result.formative_evaluation_nodes[Number(field.dataset.afDecision)].decision_by = field.value;
  });
  return result;
}

function collectActivityPresentationOverrides() {
  const overrides = {};
  const activities = serverState?.activityFormative?.design?.result?.activities || [];
  document.querySelectorAll("#activityFormativeGeneratedContent [data-af-presentation]").forEach(field => {
    const [index, key] = field.dataset.afPresentation.split(":");
    const activityId = activities[Number(index)]?.activity_id;
    if (activityId && field.value.trim()) {
      (overrides[activityId] ||= {})[key] = field.value.trim();
    }
  });
  return overrides;
}

async function loadGoalPath(teachingId, classroomId) {
  if (!teachingId || !classroomId) return;
  try {
    const payload = await window.PlatformAPI.getGoalPath(teachingId, classroomId);
    if (serverState?.current_teaching_id !== teachingId || ui.activeInterventionClassroomId !== classroomId) return;
    serverState.goalPath = payload;
    const contextKey = `${teachingId}:${classroomId}`;
    if (ui.goalPathContextKey !== contextKey) {
      ui.goalPathContextKey = contextKey;
      for (const id of ["teachingContentAnalysis", "teachingFocusAnalysis", "teachingEnvironmentConditions", "teacherLessonConception"]) {
        document.getElementById(id).value = "";
      }
    }
    if (payload.design?.teacher_instructional_context) {
      const context = payload.design.teacher_instructional_context;
      document.getElementById("teachingContentAnalysis").value = context.teaching_content_and_curriculum_analysis;
      document.getElementById("teachingFocusAnalysis").value = context.teaching_focus_and_difficulty_analysis;
      document.getElementById("teachingEnvironmentConditions").value = context.teaching_environment_and_ai_support_conditions;
      document.getElementById("teacherLessonConception").value = context.teacher_lesson_conception || "";
      document.getElementById("interventionDurationMinutes").value = context.planned_duration_minutes;
      updateInterventionDurationContext();
    }
    if (payload.teacher_analysis) {
      const analysis = payload.teacher_analysis;
      document.getElementById("teachingContentAnalysis").value = analysis.content_analysis;
      document.getElementById("teachingFocusAnalysis").value = analysis.focus_analysis;
    }
    const note = document.getElementById("teacherAnalysisSourceNote");
    const classrooms = serverState.studentResults?.teaching?.id === teachingId ? serverState.studentResults.classrooms || [] : [];
    const selectedClass = classrooms.find(item => item.id === classroomId)?.name || "当前班级";
    const readyElsewhere = classrooms.filter(item => item.id !== classroomId && serverState.studentResults?.class_reports?.[item.id]?.status === "ready");
    note.textContent = !payload.class_diagnosis_ready
      ? readyElsewhere.length
        ? `当前选中“${selectedClass}”，该班尚无可用报告。${readyElsewhere.map(item => item.name).join("、")}已有班级报告，请在上方切换班级。`
        : `当前选中“${selectedClass}”，该班尚无可用报告。请先在“诊断结果反馈”生成该班报告。`
      : payload.teacher_analysis?.source_note || "";
    note.hidden = !note.textContent;
    renderGoalPath(payload);
    if (payload.design?.status === "teacher_confirmed") loadActivityFormative(teachingId, classroomId);
  } catch (error) {
    document.getElementById("goalPathPrerequisite").textContent = `无法读取目标与路径设计：${error.message}`;
  }
}

function collectGoalPathEdits() {
  const original = serverState?.goalPath?.design?.result;
  if (!original) return null;
  const result = structuredClone(original);
  const root = document.getElementById("goalPathGeneratedContent");
  result.common_core_goal.goal_statement = root.querySelector('[data-gp-common="goal_statement"]').value.trim();
  const structure = root.querySelector("[data-gp-structure]").value.trim();
  result.common_core_goal.target_cognitive_structure.structure_and_rationale = structure;
  result.common_core_goal.observable_success_criteria = root.querySelector("[data-gp-criteria]").value.split("\n").map(item => item.trim()).filter(Boolean);
  root.querySelectorAll("[data-gp-goal]").forEach(field => {
    result.progression_goals[Number(field.dataset.gpGoal)][field.dataset.gpField] = field.value.trim();
  });
  root.querySelectorAll("[data-gp-stage-name]").forEach(field => {
    result.intervention_path.stages[Number(field.dataset.gpStageName)].stage_name = field.value.trim();
  });
  root.querySelectorAll("[data-gp-stage-duration]").forEach(field => {
    result.intervention_path.stages[Number(field.dataset.gpStageDuration)].duration_minutes = Number(field.value);
  });
  root.querySelectorAll("[data-gp-unit-field]").forEach(field => {
    result.intervention_path.stages[Number(field.dataset.gpStage)].activity_units[Number(field.dataset.gpUnit)][field.dataset.gpUnitField] = field.value.trim();
  });
  const studentLookup = new Map(result.student_goal_assignments.map(student => [student.student_id, {
    student_id: student.student_id, student_name: student.student_name
  }]));
  const organizationNames = {
    C: "全班共同活动（C）", H: "同质小组活动（H）", X: "异质小组协同（X）",
    I: "个体学习或个别支持（I）", S: "学习站轮转（S）", A: "综合应用（A）"
  };
  result.intervention_path.stages.forEach((stage, stageIndex) => {
    const organizationCounts = {};
    stage.activity_units.forEach((unit, unitIndex) => {
      const cellSelector = `[data-gp-stage="${stageIndex}"][data-gp-unit="${unitIndex}"]`;
      const code = root.querySelector(`[data-gp-org-code]${cellSelector}`).value;
      if (code !== unit.organization_code) unit.organization_name = organizationNames[code];
      unit.organization_code = code;
      organizationCounts[code] = (organizationCounts[code] || 0) + 1;
      unit.unit_id = `${stage.stage_id}-${code}${organizationCounts[code]}`;
      unit.target_students = [...root.querySelectorAll(`[data-gp-target-student]${cellSelector}:checked`)]
        .map(field => studentLookup.get(field.value)).filter(Boolean);
      unit.target_goal_ids = [...root.querySelectorAll(`[data-gp-target-goal]${cellSelector}:checked`)]
        .map(field => field.value);
    });
  });
  result.intervention_path.total_duration_minutes = result.intervention_path.stages.reduce((sum, stage) => sum + stage.duration_minutes, 0);
  return result;
}

function goalPathEditError(result) {
  const stages = result.intervention_path.stages;
  for (const [index, stage] of stages.entries()) {
    if (!stage.stage_name) return `请填写第 ${index + 1} 阶段的名称`;
    if (!Number.isInteger(stage.duration_minutes) || stage.duration_minutes < 1) return `请填写第 ${index + 1} 阶段的有效时长`;
    for (const [unitIndex, unit] of stage.activity_units.entries()) {
      const label = `第 ${index + 1} 阶段第 ${unitIndex + 1} 项活动`;
      if (unit.activity_name.length < 2 || !unit.activity_summary) return `请完善${label}的名称和内容`;
      if (!unit.target_students.length || !unit.target_goal_ids.length) return `请为${label}至少选择一名学生和一个目标`;
    }
  }
  const planned = serverState?.goalPath?.design?.teacher_instructional_context?.planned_duration_minutes;
  if (result.intervention_path.total_duration_minutes !== planned) return `课堂阶段时长总和须为 ${planned} 分钟`;
  return "";
}

function applyConfirmedGoalPathToActivities() {
  const design = serverState?.goalPath?.design;
  if (!design || design.status !== "teacher_confirmed") return;
  const key = `${design.teaching_id}:${design.classroom_id}:${design.updated_at}`;
  if (ui.goalPathAppliedKey === key) return;
  const result = design.result;
  const goals = new Map(result.progression_goals.map(goal => [goal.goal_id, goal.goal_statement]));
  const byCode = {
    C: ["全班共同活动", "全班", "师生"],
    H: ["同质小组并行", "同质小组", "生生"],
    X: ["异质小组协同", "异质小组", "生生"],
    I: ["个体独立活动", "个体", "学生—内容"],
    S: ["学习站轮转", "学习站", "生生"],
    A: ["全班共同活动", "全班", "师生"]
  };
  const activities = result.intervention_path.stages.map(stage => {
    const units = stage.activity_units;
    const code = units.find(unit => ["H", "S"].includes(unit.organization_code))?.organization_code || units[0].organization_code;
    const [structure, organization, dialogue] = byCode[code] || byCode.C;
    const goalIds = [...new Set(units.flatMap(unit => unit.target_goal_ids))];
    return {
      title: units.length === 1 ? units[0].activity_name : stage.stage_name,
      duration: stage.duration_minutes,
      structure, organization, dialogue,
      goal: goalIds.map(id => goals.get(id)).filter(Boolean).join("；"),
      task: units.map(unit => `${unit.activity_name}：${unit.activity_summary}`).join("\n"),
      implementation: "",
      support: "",
      branches: units.length > 1 && ["H", "S"].includes(code) ? units.map(unit => ({
        name: unit.activity_name,
        target: unit.target_goal_ids.join("、"),
        students: unit.target_students.map(student => student.student_name).join("、"),
        task: unit.activity_summary,
        support: ""
      })) : [],
      checkpoint: false,
      checkpointQuestion: "", checkpointEvidence: "", checkpointStandard: "",
      reached: "", notReached: "", insufficient: ""
    };
  });
  Object.keys(interventionActivities).forEach(id => delete interventionActivities[id]);
  const list = document.getElementById("sortableActivityList");
  list.replaceChildren();
  activities.forEach((activity, index) => {
    const id = String(index + 1);
    interventionActivities[id] = activity;
    const card = createInterventionActivityCard(id, index + 1);
    list.append(card);
    renderInlineActivityDetails(card, id);
  });
  ui.nextInterventionActivityId = activities.length + 1;
  ui.goalPathAppliedKey = key;
  document.getElementById("activitySequenceSource").textContent = "依据：教师已确认的目标与课堂路径";
  renumberInterventionActivities();
  updateInterventionTimeTotal();
}

function currentStudentResult() {
  return (serverState?.studentResults?.results || []).filter(
    item => item.student.classroom_id === ui.activeClassroomId
  )[ui.activeStudentResultIndex] || null;
}

async function generateReportForResult(result, { rerender = true } = {}) {
  const payload = await window.PlatformAPI.generateStudentReport(result.id);
  result.report = payload.report;
  if (rerender) renderStudentResults(serverState.studentResults);
  return payload.report;
}

function getTeachingById(teachingId) {
  return serverState?.precision_teachings.find(teaching => teaching.id === teachingId) || null;
}

const teachingSubjects = [
  "语文", "数学", "英语", "物理", "化学", "生物", "历史", "地理", "思想政治",
  "道德与法治", "科学", "信息科技", "通用技术", "体育与健康", "音乐", "美术", "艺术", "综合实践活动"
];

function populateTeachingSubjects(selectedSubject) {
  const select = document.getElementById("blockSubject");
  const subjects = new Set(teachingSubjects);
  if (serverState?.teacher?.subject) subjects.add(serverState.teacher.subject);
  for (const teaching of serverState?.precision_teachings || []) {
    if (teaching.subject) subjects.add(teaching.subject);
  }
  if (selectedSubject) subjects.add(selectedSubject);
  select.replaceChildren(...[...subjects].map(subject => new Option(subject, subject)));
  select.value = selectedSubject || serverState?.teacher?.subject || "数学";
}

function updateCurrentTeachingContext(teaching) {
  const sidebar = document.querySelector(".current-teaching-context");
  const title = sidebar.querySelector("strong");
  const meta = sidebar.querySelector("small");
  if (!teaching) {
    title.textContent = "尚未选择精准教学";
    meta.textContent = "请先新建一个项目";
    return;
  }
  const status = teaching.status === "active" ? "进行中" : teaching.status === "completed" ? "已完成" : "草稿";
  title.textContent = teaching.title;
  meta.textContent = `${teaching.grade}${teaching.subject} · ${status}`;
}

function populateTeachingEditor(teaching) {
  if (!teaching) return;
  document.getElementById("blockTheme").value = teaching.title || "";
  document.getElementById("blockGoal").value = teaching.goal || "";
  document.getElementById("blockContent").value = teaching.content || "";
  document.getElementById("blockRationale").value = teaching.rationale || "";
  populateTeachingSubjects(teaching.subject);
  document.getElementById("blockGrade").value = teaching.grade || "高一";
  document.getElementById("blockTextbook").value = teaching.textbook || "";
  document.getElementById("blockPeriods").value = teaching.estimated_periods || 1;
}

function renderTeachingSelector() {
  const select = document.getElementById("currentTeachingSelect");
  select.replaceChildren();
  if (!serverState.precision_teachings.length) {
    const option = document.createElement("option");
    option.textContent = "暂无精准教学";
    option.value = "";
    select.append(option);
    select.disabled = true;
    updateCurrentTeachingContext(null);
    return;
  }
  select.disabled = false;
  serverState.precision_teachings.forEach(teaching => {
    const option = document.createElement("option");
    option.value = teaching.id;
    option.textContent = `${teaching.status === "active" ? "进行中" : teaching.status === "completed" ? "已完成" : "草稿"} · ${teaching.grade}${teaching.subject} · ${teaching.title}`;
    select.append(option);
  });
  const fallbackId = serverState.precision_teachings[0].id;
  if (!getTeachingById(serverState.current_teaching_id)) serverState.current_teaching_id = fallbackId;
  select.value = serverState.current_teaching_id;
  updateCurrentTeachingContext(getTeachingById(serverState.current_teaching_id));
}

function stageAccessible(pageName) {
  if (!["diagnosis", "feedback", "intervention"].includes(pageName)) return true;
  if (!serverState?.current_workspace) return false;
  return pageName === "diagnosis" || Boolean(serverState.current_workspace.stages[pageName].accessible);
}

function renderStageNavigation(workspace) {
  ["diagnosis", "feedback", "intervention"].forEach(stage => {
    const button = document.querySelector(`.nav-item[data-page="${stage}"]`);
    const accessible = Boolean(workspace?.stages[stage].accessible);
    button.disabled = !accessible;
    button.classList.toggle("locked", !accessible);
    button.title = accessible ? "" : !workspace ? "请先创建精准教学" : stage === "feedback" ? "发布诊断任务后开放" : "确认诊断反馈后开放";
  });
}

function applyWorkspace(workspace) {
  const diagnosis = workspace.diagnosis;
  renderPublishClassrooms();
  ui.diagnosisType = diagnosis.diagnosis_type || "pre";
  document.getElementById("diagnosisTaskText").value = diagnosis.task_text || "";
  document.getElementById("diagnosisRoleText").value = diagnosis.ai_role || "";
  document.getElementById("diagnosisDuration").value = diagnosis.duration_minutes || 15;
  document.getElementById("publishDuration").textContent = `${diagnosis.duration_minutes || 15} 分钟`;
  const typeCopy = {
    pre: ["课前诊断", "本次结果将作为课堂干预设计的主要证据来源。"],
    during: ["课中诊断", "本次结果用于判断当前活动效果并调整后续活动。"],
    post: ["课后诊断", "本次结果将与课前证据比较，用于分析本轮干预后的表现变化。"]
  }[ui.diagnosisType];
  document.getElementById("publishDiagnosisType").textContent = typeCopy[0];
  const publishButton = document.getElementById("publishTaskButton");
  publishButton.textContent = diagnosis.status === "published" ? "更新已发布任务" : "确认发布";
  renderRubric(workspace.rubric);
  const analysisRubricStep = document.getElementById("analysisRubricStep");
  const rubricConfirmedForAnalysis = workspace.rubric?.status === "confirmed";
  analysisRubricStep.classList.toggle("done", rubricConfirmedForAnalysis);
  document.getElementById("analysisRubricStatus").textContent = rubricConfirmedForAnalysis ? "量规已确认" : "等待量规确认";

  const feedback = workspace.feedback;
  const hasFeedbackData = Boolean(feedback?.has_data);
  document.getElementById("feedbackEmptyState").hidden = hasFeedbackData;
  document.getElementById("feedbackWorkspaceContent").hidden = !hasFeedbackData;
  document.getElementById("completeFeedbackButton").disabled = !hasFeedbackData || feedback?.status === "confirmed";
  document.getElementById("completeFeedbackButton").textContent = feedback?.status === "confirmed" ? "反馈已确认" : "确认反馈并进入干预设计";
  const feedbackName = document.getElementById("feedbackTeachingName");
  feedbackName.replaceChildren(new Option(workspace.teaching.title, workspace.teaching.id, true, true));
  const feedbackTask = document.getElementById("feedbackTaskName");
  const taskLabel = diagnosis.task_text ? `${typeCopy[0]} · ${diagnosis.task_text.slice(0, 18)}${diagnosis.task_text.length > 18 ? "…" : ""}` : typeCopy[0];
  feedbackTask.replaceChildren(new Option(taskLabel, diagnosis.teaching_id, true, true));
  document.getElementById("feedbackSubmissionStatus").textContent = hasFeedbackData
    ? `${feedback.total_students}/${feedback.total_students} 已提交`
    : "等待学生提交";

  document.getElementById("interventionTeachingName").replaceChildren(new Option(workspace.teaching.title, workspace.teaching.id, true, true));
  document.getElementById("interventionTaskName").replaceChildren(new Option(taskLabel, diagnosis.teaching_id, true, true));
  const interventionEvidenceStatus = document.getElementById("interventionEvidenceStatus");
  interventionEvidenceStatus.textContent = workspace.stages.intervention.accessible ? "诊断结果已确认" : "等待反馈确认";
  interventionEvidenceStatus.className = workspace.stages.intervention.accessible ? "status success" : "status neutral";

  if (workspace.intervention) {
    document.getElementById("interventionDurationMinutes").value = workspace.intervention.duration_minutes || 45;
    updateInterventionDurationContext();
  }
  renderStageNavigation(workspace);
  loadStudentResults(workspace.teaching.id);
}

async function chooseCurrentTeaching(teachingId, { persist = true } = {}) {
  const teaching = getTeachingById(teachingId);
  if (!teaching) throw new Error("精准教学不存在或已被删除");
  if (persist) await window.PlatformAPI.selectTeaching(teachingId);
  if (serverState.current_teaching_id !== teachingId) {
    serverState.activityFormative = null;
    ui.activityFormativeError = null;
    ui.diagnosticRecommendations = null;
    renderDiagnosticRecommendations(null);
  }
  serverState.current_teaching_id = teachingId;
  serverState.current_workspace = await window.PlatformAPI.getWorkspace(teachingId);
  const workspaceTeaching = serverState.current_workspace.teaching;
  serverState.precision_teachings = serverState.precision_teachings.map(item => item.id === teachingId ? workspaceTeaching : item);
  document.getElementById("currentTeachingSelect").value = teachingId;
  updateCurrentTeachingContext(workspaceTeaching);
  populateTeachingEditor(workspaceTeaching);
  applyWorkspace(serverState.current_workspace);
  loadDiagnosticRecommendations(teachingId);
  renderTeachingList(serverState.precision_teachings);
  return workspaceTeaching;
}

function storeWorkspace(workspace) {
  serverState.current_workspace = workspace;
  serverState.precision_teachings = serverState.precision_teachings.map(item => item.id === workspace.teaching.id ? workspace.teaching : item);
  renderTeachingSelector();
  renderTeachingList(serverState.precision_teachings);
  updateCurrentTeachingContext(workspace.teaching);
  applyWorkspace(workspace);
}

function diagnosisFormPayload(status) {
  return {
    diagnosis_type: ui.diagnosisType,
    // Keep the existing stored goal for published tasks so hiding the duplicate
    // editor never invalidates an already-confirmed rubric.
    goal: serverState.current_workspace?.diagnosis?.goal || serverState.current_workspace?.teaching?.goal || "",
    task_text: document.getElementById("diagnosisTaskText").value.trim(),
    ai_role: document.getElementById("diagnosisRoleText").value.trim(),
    duration_minutes: Number(document.getElementById("diagnosisDuration").value || 15),
    status,
    classroom_ids: status === "published"
      ? [...document.querySelectorAll('#publishClassOptions input[name="publishClassroom"]:checked')].map(input => input.value)
      : null
  };
}

async function saveCurrentDiagnosis(status) {
  if (!serverState?.current_teaching_id) throw new Error("请先选择精准教学");
  const workspace = await window.PlatformAPI.saveDiagnosis(serverState.current_teaching_id, diagnosisFormPayload(status));
  storeWorkspace(workspace);
  return workspace;
}

const rubricLevelNames = { P: "P 前结构", U: "U 单点结构", M: "M 多点结构", R: "R 关联结构", EA: "EA 拓展抽象" };

function rubricListText(items) {
  return Array.isArray(items) ? items.join("\n") : "";
}

function renderRubricAnalysis(rubric) {
  const panel = document.getElementById("rubricAnalysisContent");
  if (!rubric) {
    panel.innerHTML = "<p class=\"muted\">生成后可查看任务要素、关键关系、抽象机会与任务上限。</p>";
    return;
  }
  const analysis = rubric.task_analysis || {};
  const elements = (analysis.elements || []).map(item => `<li><b>${escapeHTML(item.id)} · ${escapeHTML(item.name)}</b>：${escapeHTML(item.meaning)}</li>`).join("");
  const relations = (analysis.relations?.items || []).map(item => `<li><b>${escapeHTML(item.id)}</b>：${escapeHTML(item.relation_statement)}</li>`).join("");
  const abstractions = (analysis.abstractions || []).map(item => `<li><b>${escapeHTML(item.id)}</b>：${escapeHTML(item.description)}</li>`).join("");
  const affordance = analysis.task_affordance || {};
  panel.innerHTML = `<p><b>真实认知要求：</b>${escapeHTML(analysis.true_cognitive_demand || "未说明")}</p>
    <div class="rubric-analysis-grid"><section><h4>任务要素</h4><ul>${elements || "<li>未识别</li>"}</ul></section>
    <section><h4>关键关系</h4><ul>${relations || "<li>未识别</li>"}</ul></section>
    <section><h4>抽象与迁移机会</h4><ul>${abstractions || "<li>当前任务未提供</li>"}</ul></section></div>
    <p><b>任务可合理引出的最高层级：</b>${escapeHTML(rubricLevelNames[affordance.highest_reasonably_elicitable_level] || affordance.highest_reasonably_elicitable_level || "未判断")}</p>`;
}

function renderRubric(record) {
  const body = document.getElementById("rubricTableBody");
  const status = document.getElementById("rubricRuntimeStatus");
  const confirm = document.getElementById("rubricConfirmed");
  const next = document.getElementById("rubricNextButton");
  const generate = document.getElementById("regenerateRubric");
  const details = document.getElementById("rubricAnalysisDetails");
  if (!record?.rubric) {
    body.innerHTML = '<tr><td colspan="4" class="muted">进入本步骤后，系统会依据当前精准教学内容和诊断任务生成五级标准。</td></tr>';
    status.hidden = true;
    status.textContent = "";
    status.className = "rubric-runtime-status";
    confirm.checked = false;
    confirm.disabled = true;
    next.disabled = true;
    generate.disabled = ui.rubricGenerating;
    generate.textContent = ui.rubricGenerating ? "AI 正在生成…" : "AI 生成量规";
    details.hidden = true;
    renderRubricAnalysis(null);
    return;
  }
  body.innerHTML = Object.keys(rubricLevelNames).map(level => {
    const row = record.rubric.levels[level];
    const detail = [
      ...(row.adjacent_boundaries || []).map(item => `边界：${item}`),
      ...(row.typical_expressions || []).map(item => `示例：${item}`),
      `追溯：${(row.trace_to || []).join("、")}`,
      ...(row.limitations || []).map(item => `限制：${item}`)
    ].join("\n");
    return `<tr data-rubric-level="${level}"><th>${rubricLevelNames[level]}${row.status === "not_reasonably_elicitable" ? "<small>当前任务难以引出</small>" : ""}</th>
      <td contenteditable="true" data-rubric-field="core_performance">${escapeHTML(rubricListText(row.core_performance))}</td>
      <td contenteditable="true" data-rubric-field="decision_evidence">${escapeHTML(rubricListText(row.decision_evidence))}</td>
      <td class="rubric-trace-cell">${escapeHTML(detail).replaceAll("\n", "<br>")}</td></tr>`;
  }).join("");
  const confirmed = record.status === "confirmed";
  const stale = record.status === "stale";
  status.hidden = !(stale || confirmed);
  status.textContent = stale ? "任务内容已变化，请重新生成量规" : confirmed ? "已确认" : "";
  status.className = `rubric-runtime-status ${stale ? "stale" : confirmed ? "confirmed" : "draft"}`;
  confirm.checked = confirmed;
  confirm.disabled = stale;
  next.disabled = !confirmed;
  generate.disabled = ui.rubricGenerating;
  generate.textContent = ui.rubricGenerating ? "AI 正在生成…" : "AI 重新生成";
  details.hidden = false;
  renderRubricAnalysis(record.rubric);
}

function rubricFromEditor() {
  const record = serverState.current_workspace?.rubric;
  if (!record?.rubric) throw new Error("请先生成任务专用量规");
  const rubric = structuredClone(record.rubric);
  document.querySelectorAll("#rubricTableBody [data-rubric-level]").forEach(row => {
    const level = row.dataset.rubricLevel;
    ["core_performance", "decision_evidence"].forEach(field => {
      rubric.levels[level][field] = row.querySelector(`[data-rubric-field="${field}"]`).innerText
        .split("\n").map(item => item.trim()).filter(Boolean);
    });
  });
  return rubric;
}

async function persistRubric(confirmed, { rerender = true } = {}) {
  const result = await window.PlatformAPI.saveRubric(serverState.current_teaching_id, rubricFromEditor(), confirmed);
  serverState.current_workspace.rubric = result.rubric;
  if (rerender) renderRubric(result.rubric);
  return result.rubric;
}

async function generateCurrentRubric() {
  const current = serverState.current_workspace?.diagnosis;
  const form = diagnosisFormPayload("draft");
  const changed = !current || ["diagnosis_type", "goal", "task_text", "ai_role", "duration_minutes"]
    .some(field => String(current[field] ?? "") !== String(form[field] ?? ""));
  if (changed) await saveCurrentDiagnosis("draft");
  const result = await window.PlatformAPI.generateRubric(serverState.current_teaching_id);
  serverState.current_workspace.rubric = result.rubric;
  renderRubric(result.rubric);
  return result.rubric;
}

async function openRubricStep() {
  setDiagnosisStep(3);
  if (ui.rubricGenerating) return;
  const rubric = serverState.current_workspace?.rubric;
  if (rubric?.rubric && rubric.status !== "stale") {
    renderRubric(rubric);
    return;
  }
  ui.rubricGenerating = true;
  renderRubric(rubric);
  try {
    await generateCurrentRubric();
    showToast("已根据当前任务生成 SOLO 分析标准，请检查后确认");
  } catch (error) {
    renderRubric(serverState.current_workspace?.rubric);
    showToast(`量规生成失败：${error.message}`);
  } finally {
    ui.rubricGenerating = false;
    renderRubric(serverState.current_workspace?.rubric);
  }
}

function renderTeachingList(teachings) {
  const list = document.getElementById("teachingList");
  list.classList.toggle("is-empty", !teachings.length);
  if (!teachings.length) {
    list.innerHTML = needsProfileSetup() ? `<article class="card teaching-empty-state">
      <span class="teaching-empty-icon" aria-hidden="true">▦</span>
      <h2>先完善教师与班级信息</h2>
      <p>${!serverState.teacher.profile_completed && !serverState.classrooms.length ? "保存教师教学信息，并添加至少一个班级。" : !serverState.teacher.profile_completed ? "请先保存教师教学信息。" : "请先添加至少一个班级。"}完成后就可以开启第一项精准教学。</p>
      <button class="button primary" type="button" data-page="profile">填写教师与班级信息</button>
    </article>` : `<article class="card teaching-empty-state">
      <span class="teaching-empty-icon" aria-hidden="true">▦</span>
      <h2>从第一项精准教学开始</h2>
      <p>确定一个希望促进的思维发展目标，再逐步设计诊断、反馈与课堂干预。</p>
      <button class="button primary" type="button" data-page="block-edit">＋ 开启新的精准教学</button>
    </article>`;
    return;
  }
  list.innerHTML = teachings.map(teaching => {
    const title = escapeHTML(teaching.title);
    const goal = escapeHTML(teaching.goal);
    const meta = `${escapeHTML(teaching.grade)}${escapeHTML(teaching.subject)}${teaching.textbook ? ` · ${escapeHTML(teaching.textbook)}` : ""}`;
    const updatedAt = escapeHTML(formatUpdatedAt(teaching.updated_at));
    if (teaching.status === "draft") {
      return `<article class="teaching-item card compact${teaching.id === serverState.current_teaching_id ? " current-teaching-card" : ""}" data-teaching-id="${escapeHTML(teaching.id)}">
        <div class="teaching-item-main">
          <div class="eyebrow"><span class="status neutral">草稿</span><span>${meta} · 预计 ${Number(teaching.estimated_periods)} 课时</span></div>
          <h2>${title}</h2><p class="teaching-goal muted">${goal}</p>
        </div>
        <footer class="teaching-item-footer"><span>最近更新：${updatedAt}</span><div class="teaching-footer-actions"><button class="button small danger" data-delete-teaching="${escapeHTML(teaching.id)}" data-teaching-title="${title}">删除</button><button class="button" data-page="block-edit" data-open-teaching="${escapeHTML(teaching.id)}">继续编辑</button></div></footer>
      </article>`;
    }
    const diagnosisDone = teaching.diagnosis_status === "published";
    const feedbackOpen = teaching.feedback_status !== "locked";
    const feedbackDone = teaching.feedback_status === "confirmed";
    const interventionOpen = teaching.intervention_status !== "locked";
    const interventionDone = teaching.intervention_status === "completed";
    const projectLabel = teaching.status === "completed" ? "已完成" : "进行中";
    return `<article class="teaching-item card${teaching.id === serverState.current_teaching_id ? " current-teaching-card" : ""}" data-teaching-id="${escapeHTML(teaching.id)}">
      <div class="teaching-item-main">
        <div class="eyebrow"><span class="status success">${projectLabel}</span><span>${meta}</span><span>预计 ${Number(teaching.estimated_periods)} 课时</span></div>
        <h2>${title}</h2><p class="teaching-goal">${goal}</p>
        <div class="teaching-progress" aria-label="精准教学进度">
          <button class="${diagnosisDone ? "complete" : "current"}" data-page="diagnosis" data-open-teaching="${escapeHTML(teaching.id)}"><span>1</span><b>精准诊断任务设计与发布</b><small>${diagnosisDone ? "任务已发布" : "待设计"}</small></button>
          <button class="${feedbackDone ? "complete" : feedbackOpen ? "current" : "locked"}" data-page="feedback" data-open-teaching="${escapeHTML(teaching.id)}" ${feedbackOpen ? "" : "disabled"}><span>2</span><b>诊断结果反馈</b><small>${feedbackDone ? "结果已确认" : feedbackOpen ? "待确认" : "完成诊断后开放"}</small></button>
          <button class="${interventionDone ? "complete" : interventionOpen ? "current" : "locked"}" data-page="intervention" data-open-teaching="${escapeHTML(teaching.id)}" ${interventionOpen ? "" : "disabled"}><span>3</span><b>精准干预设计</b><small>${interventionDone ? "方案已完成" : interventionOpen ? "方案设计中" : "完成反馈后开放"}</small></button>
        </div>
      </div>
      <footer class="teaching-item-footer"><span>最近更新：${updatedAt}</span><div class="teaching-footer-actions"><button class="button small danger" data-delete-teaching="${escapeHTML(teaching.id)}" data-teaching-title="${title}">删除</button><button class="button primary" data-page="${teaching.current_stage || "diagnosis"}" data-open-teaching="${escapeHTML(teaching.id)}">继续精准教学</button></div></footer>
    </article>`;
  }).join("");
}

async function hydrateFromServer() {
  try {
    serverState = await window.PlatformAPI.bootstrap();
    document.getElementById("teacherProfileButton").title = `${serverState.teacher.school_name || ""} · ${serverState.teacher.subject}`;
    populateTeachingSubjects(serverState.teacher.subject);
    if (serverState.current_teaching_id) {
      serverState.current_workspace = await window.PlatformAPI.getWorkspace(serverState.current_teaching_id);
      serverState.precision_teachings = serverState.precision_teachings.map(item => item.id === serverState.current_teaching_id ? serverState.current_workspace.teaching : item);
    }
    updateTeacherIdentity(serverState.teacher);
    renderClassrooms(serverState.classrooms);
    setupClassroomCreation();
    renderTeachingSelector();
    renderTeachingList(serverState.precision_teachings);
    if (serverState.current_teaching_id) {
      populateTeachingEditor(getTeachingById(serverState.current_teaching_id));
    } else {
      for (const id of ["blockTheme", "blockGoal", "blockContent", "blockRationale", "blockTextbook"]) {
        document.getElementById(id).value = "";
      }
      document.getElementById("blockPeriods").value = 1;
    }
    if (serverState.current_workspace) applyWorkspace(serverState.current_workspace);
    else {
      renderStageNavigation(null);
      showPage("blocks");
    }
    if (serverState.current_teaching_id) loadDiagnosticRecommendations(serverState.current_teaching_id);
    const runtime = serverState.runtime || {};
    window.LLMDebug?.start(runtime.llm_request_debug);
    const aiProvider = runtime.ai_provider === "deepseek"
      ? "DeepSeek"
      : runtime.ai_provider === "mock" ? "Mock AI" : (runtime.ai_provider || "AI 未配置");
    const provider = `服务已连接 · ${aiProvider}`;
    setBackendStatus("online", provider);
  } catch (error) {
    setBackendStatus("offline", "本地服务未连接");
    console.error("Failed to bootstrap application", error);
  }
}

function showTeacherAuth({ message = "" } = {}) {
  document.querySelector(".app-shell").inert = true;
  const screen = document.getElementById("teacherAuthScreen");
  screen.hidden = false;
  document.getElementById("teacherAuthLoading").hidden = true;
  document.getElementById("teacherAuthForm").hidden = false;
  document.getElementById("teacherAuthSubmit").textContent = "进入工作台";
  const error = document.getElementById("teacherAuthError");
  error.textContent = message;
  error.hidden = !message;
  document.getElementById("teacherAuthSchool").focus();
}

async function loadTeacherLoginOptions() {
  const options = await window.PlatformAPI.getTeacherLoginOptions();
  for (const [listId, values] of [
    ["teacherSchoolOptions", options.schools || []],
    ["teacherSubjectOptions", options.subjects || []]
  ]) {
    const list = document.getElementById(listId);
    list.replaceChildren(...values.map(value => {
      const option = document.createElement("option");
      option.value = value;
      return option;
    }));
  }
}

async function initializeTeacherAuth() {
  try {
    const status = await window.PlatformAPI.getTeacherAuthStatus();
    if (status.authenticated) {
      document.getElementById("teacherAuthScreen").hidden = true;
      document.querySelector(".app-shell").inert = false;
      await hydrateFromServer();
    } else {
      showTeacherAuth();
      try {
        await loadTeacherLoginOptions();
      } catch (error) {
        const warning = document.getElementById("teacherAuthError");
        warning.textContent = `无法加载学校和学科选项：${error.message}`;
        warning.hidden = false;
      }
    }
  } catch (error) {
    document.getElementById("teacherAuthLoading").textContent = `无法检查登录状态：${error.message}`;
  }
}

document.getElementById("teacherAuthForm").addEventListener("submit", async event => {
  event.preventDefault();
  const school = document.getElementById("teacherAuthSchool").value.trim();
  const subject = document.getElementById("teacherAuthSubject").value.trim();
  const name = document.getElementById("teacherAuthName").value.trim();
  const error = document.getElementById("teacherAuthError");
  const button = document.getElementById("teacherAuthSubmit");
  button.disabled = true;
  error.hidden = true;
  try {
    await window.PlatformAPI.loginTeacher(school, subject, name);
    document.getElementById("teacherAuthScreen").hidden = true;
    document.querySelector(".app-shell").inert = false;
    await hydrateFromServer();
  } catch (problem) {
    error.textContent = problem.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
  }
});

document.getElementById("teacherLogoutButton").addEventListener("click", async () => {
  try { await window.PlatformAPI.logoutTeacher(); }
  finally { window.location.reload(); }
});

window.addEventListener("teacher-session-expired", () => {
  showTeacherAuth({ message: "登录已过期，请重新登录。" });
  loadTeacherLoginOptions().catch(() => {});
});

function showPage(pageName) {
  ui.currentPage = pageName;
  pages.forEach(page => page.classList.toggle("active", page.id === `page-${pageName}`));
  document.querySelectorAll(".nav-item").forEach(button => {
    button.classList.toggle("active", button.dataset.page === pageName && !button.dataset.openOutput);
  });
  document.getElementById("sidebar").classList.remove("open");
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (pageName === "feedback" && serverState?.current_teaching_id) {
    loadStudentResults(serverState.current_teaching_id);
  }
  if (pageName === "intervention" && serverState?.current_teaching_id) {
    if (ui.activeInterventionClassroomId) loadGoalPath(serverState.current_teaching_id, ui.activeInterventionClassroomId);
    else loadStudentResults(serverState.current_teaching_id);
    if (ui.interventionStep === 3 && ui.activeInterventionClassroomId) loadActivityFormative(serverState.current_teaching_id, ui.activeInterventionClassroomId);
  }
}

function setDiagnosisStep(step) {
  ui.diagnosisStep = Number(step);
  document.querySelectorAll("[data-dstep]").forEach(button => button.classList.toggle("active", Number(button.dataset.dstep) === ui.diagnosisStep));
  document.querySelectorAll("[data-dpanel]").forEach(panel => panel.classList.toggle("active", Number(panel.dataset.dpanel) === ui.diagnosisStep));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function setInterventionStep(step) {
  const targetStep = Number(step);
  if (targetStep >= 3 && targetStep <= 4) {
    const design = serverState?.goalPath?.design;
    const legacyCompleted = !design && serverState?.current_workspace?.intervention?.status === "completed";
    if (design?.status !== "teacher_confirmed" && !legacyCompleted) {
      showToast("请先生成并确认目标与活动路径");
      return;
    }
    if (targetStep === 4 && (serverState?.activityFormative?.design?.status !== "teacher_confirmed" || ui.activityFormativeDirty) && !legacyCompleted) {
      showToast("请先确认学习活动与形成性评价");
      return;
    }
    if (targetStep === 3) loadActivityFormative(serverState.current_teaching_id, ui.activeInterventionClassroomId);
  }
  ui.interventionStep = targetStep;
  document.querySelectorAll("[data-istep]").forEach(button => button.classList.toggle("active", Number(button.dataset.istep) === ui.interventionStep));
  document.querySelectorAll("[data-ipanel]").forEach(panel => panel.classList.toggle("active", Number(panel.dataset.ipanel) === ui.interventionStep));
  if (targetStep === 4) renderInterventionReport();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function setOutputTab(name) {
  document.querySelectorAll("[data-output-tab]").forEach(button => button.classList.toggle("active", button.dataset.outputTab === name));
  document.querySelectorAll("[data-output-panel]").forEach(panel => panel.classList.toggle("active", panel.dataset.outputPanel === name));
}

function setFeedbackTab(name) {
  document.querySelectorAll("[data-feedback-tab]").forEach(button => button.classList.toggle("active", button.dataset.feedbackTab === name));
  document.querySelectorAll("[data-feedback-panel]").forEach(panel => panel.classList.toggle("active", panel.dataset.feedbackPanel === name));
}

function openTaskGuide(name) {
  const modal = document.getElementById("taskGuideModal");
  const titles = { principles: "设计原则", strategies: "策略卡片" };
  document.getElementById("taskGuideTitle").textContent = titles[name];
  document.querySelectorAll("[data-task-guide-panel]").forEach(panel => {
    panel.hidden = panel.dataset.taskGuidePanel !== name;
  });
  modal.hidden = false;
  document.body.classList.add("task-guide-open");
}

function closeTaskGuide() {
  document.getElementById("taskGuideModal").hidden = true;
  document.body.classList.remove("task-guide-open");
}

function closeHelpPopovers() {
  document.querySelectorAll(".help-popover:not([hidden])").forEach(popover => popover.hidden = true);
  document.querySelectorAll("[data-help][aria-expanded='true']").forEach(button => button.setAttribute("aria-expanded", "false"));
}

function updateTimeTotal() {
  const total = Array.from(document.querySelectorAll(".minute-input")).reduce((sum, input) => sum + (Number(input.value) || 0), 0);
  const badge = document.getElementById("timeTotal");
  badge.textContent = `${total} 分钟`;
  badge.classList.toggle("alert", total !== 45);
}

function getSelectedFusionStrategies() {
  return Array.from(document.querySelectorAll("[data-fusion-strategy]:checked")).map(input => input.dataset.fusionStrategy);
}

function updateFusionSummary() {
  const selected = getSelectedFusionStrategies();
  document.querySelectorAll("[data-fusion-strategy]").forEach(input => {
    input.closest("label").classList.toggle("selected", input.checked);
  });
  document.getElementById("fusionCount").textContent = `已选择${selected.length}/2项`;
  const labels = selected.map(key => interventionFusionLabels[key]);
  document.getElementById("sequenceGenerationStatus").textContent = labels.length ? `已融合：${labels.join("、")}` : "未选择融合策略";
}

function getDesiredInterventionDuration() {
  return Math.max(10, Number(document.getElementById("interventionDurationMinutes")?.value) || 45);
}

function updateInterventionDurationContext() {
  const minutes = getDesiredInterventionDuration();
  const summary = document.getElementById("interventionDurationSummary");
  if (summary) summary.textContent = `基于诊断结果设计共${minutes}分钟的课堂方案。`;
  const brief = document.getElementById("pathDesignBrief");
  if (brief) brief.value = brief.value.replace(/【现实条件】全课\d+分钟(?:（\d+课时）)?/, `【现实条件】全课${minutes}分钟`);
  updateInterventionTimeTotal();
}

function buildPathDesignBrief(pathKey) {
  const path = interventionPathContent[pathKey];
  const fusions = getSelectedFusionStrategies().map(key => interventionFusionLabels[key]).filter(Boolean);
  const minutes = getDesiredInterventionDuration();
  return `【本节课目标】围绕完整最值论证，使学生能够连接约束条件、基本不等式、取等条件与最大值结论。

【整体推进结构】以${path.name}为主要路径（${path.diagram}）。${path.brief}

【差异化组织】课堂阶段按顺序推进；需要分层学习时，在同一阶段内设置多个同质小组或学习站同时开展，写明每组学生、进阶目标、任务、支架与汇合条件。${fusions.length ? `本课还融合：${fusions.join("、")}。` : ""}

【教师、同伴与AI分工】写明教师重点支持谁、同伴如何解释或质疑、AI在哪些条件下介入，以及AI不能替代的教师判断和学生思考。

【形成性诊断与动态调整】写明检查点的位置、需要收集的证据和判断标准；分别说明达到、未达到和证据不足时的后续行动。

【现实条件】全课${minutes}分钟，40名学生，每组可使用1台平板，同时保留纸质学习单。`;
}

function selectPrimaryPath(pathKey) {
  const selectedPath = interventionPathContent[pathKey];
  if (!selectedPath) return;
  ui.selectedPrimaryPath = pathKey;
  document.querySelectorAll("[data-primary-path]").forEach(card => {
    const selected = card.dataset.primaryPath === pathKey;
    card.classList.toggle("selected", selected);
    const button = card.querySelector("[data-select-primary-path]");
    button.textContent = selected ? "已设为主要路径" : "设为主要路径";
    button.classList.toggle("primary", selected);
  });
  document.querySelectorAll("[data-fusion-strategy]").forEach(input => {
    const duplicatesPrimaryPath = input.dataset.fusionStrategy === pathKey;
    if (duplicatesPrimaryPath) input.checked = false;
    input.disabled = duplicatesPrimaryPath;
    input.closest("label").title = duplicatesPrimaryPath ? "该机制已作为主要路径，无需重复选择" : "";
  });
  document.getElementById("selectedPathSummary").textContent = `主要路径：${selectedPath.name}`;
  document.getElementById("pathDesignBrief").value = buildPathDesignBrief(pathKey);
  updateFusionSummary();
}

function openInterventionHelp(key) {
  const content = interventionHelpContent[key];
  if (!content) return;
  const modal = document.getElementById("interventionHelpModal");
  const body = document.getElementById("interventionHelpContent");
  document.getElementById("interventionHelpTitle").textContent = content.title;
  body.replaceChildren();
  content.sections.forEach(([heading, copy]) => {
    const title = document.createElement("h3");
    const paragraph = document.createElement("p");
    title.textContent = heading;
    paragraph.textContent = copy;
    body.append(title, paragraph);
  });
  modal.hidden = false;
  document.body.classList.add("task-guide-open");
}

function openPathDetails(pathKey) {
  const path = interventionPathContent[pathKey];
  if (!path) return;
  const modal = document.getElementById("interventionHelpModal");
  const body = document.getElementById("interventionHelpContent");
  document.getElementById("interventionHelpTitle").textContent = path.name;
  body.replaceChildren();
  const diagram = document.createElement("div");
  diagram.className = "path-detail-diagram";
  diagram.textContent = path.diagram;
  body.append(diagram);
  path.details.forEach(([heading, copy]) => {
    const title = document.createElement("h3");
    const paragraph = document.createElement("p");
    title.textContent = heading;
    paragraph.textContent = copy;
    body.append(title, paragraph);
  });
  modal.hidden = false;
  document.body.classList.add("task-guide-open");
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_error) {
    const field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.append(field);
    field.select();
    const copied = document.execCommand("copy");
    field.remove();
    return copied;
  }
}

function closeInterventionHelp() {
  document.getElementById("interventionHelpModal").hidden = true;
  document.body.classList.remove("task-guide-open");
}

function updateInterventionTimeTotal() {
  const total = Array.from(document.querySelectorAll(".intervention-minute-input")).reduce((sum, input) => sum + (Number(input.value) || 0), 0);
  const badge = document.getElementById("interventionTimeTotal");
  if (!badge) return;
  badge.textContent = `${total}分钟`;
  badge.classList.toggle("alert", total !== getDesiredInterventionDuration());
}

function createDefaultActivityBranches(structure = "同质小组并行") {
  if (structure === "学习站轮转") {
    return [
      { name: "教师支持站", target: "M 多点结构目标", students: "P、U学生", task: "在教师引导下识别条件、目标量和关键要素", support: "情境图、要素卡、教师短时指导" },
      { name: "关系建构站", target: "R 关联结构目标", students: "M学生", task: "重排论证卡并绘制关系图", support: "关系框架、同伴解释" },
      { name: "迁移挑战站", target: "EA 拓展抽象目标", students: "R学生", task: "改变条件并检验方法边界", support: "新条件、反例、AI按需质疑" }
    ];
  }
  return [
    { name: "A 基础支持组", target: "M 多点结构目标", students: "P、U学生", task: "识别约束、目标量和关键要素", support: "情境图、要素卡、教师短时指导" },
    { name: "B 关系建构组", target: "R 关联结构目标", students: "M学生", task: "重排论证卡并绘制关系图", support: "关系框架、同伴解释" },
    { name: "C 迁移挑战组", target: "EA 拓展抽象目标", students: "R学生", task: "改变条件并检验方法边界", support: "新条件、反例、AI按需质疑" }
  ];
}

const interventionSequenceBlueprints = {
  progressive: [
    ["校准完整论证标准", 6, "全班共同活动", "全班", "师生", "识别完整最值论证需要建立的关键关系。", "比较典型诊断作答，区分数值结果与完整证明。"],
    ["按诊断结果分层建构", 14, "同质小组并行", "同质小组", "生生", "完成与当前思维结构相邻的进阶目标。", "基础组识别要素，建构组连接关系，挑战组检验条件。"],
    ["解释质疑并整合关系", 10, "异质小组协同", "异质小组", "生生机", "通过解释与质疑形成有依据的完整论证。", "交换分层成果，围绕条件、依据和关系修改论证。"],
    ["撤除支架独立复诊", 10, "个体独立活动", "个体", "学生—内容", "独立形成可与课前诊断比较的新证据。", "完成同构问题的模型、理由、取等条件和结论。"],
    ["回看进阶并总结迁移", 5, "全班共同活动", "全班", "师生", "概括可迁移的最值论证结构。", "比较课前与课中成果，说明新增或重组的关系。"]
  ],
  embedded: [
    ["共同进入核心论证任务", 8, "全班共同活动", "全班", "师生", "明确共同任务与成功标准。", "独立判断一份论证是否完整并标出依据。"],
    ["主线学习与靶向支持", 12, "同质小组并行", "同质小组", "师生", "多数学生推进主任务，少数学生补足关键基础。", "主线组继续关系建构；支持组接受教师短时再教学。"],
    ["支持组回归共同任务", 10, "异质小组协同", "异质小组", "生生", "在共同标准下解释并修正论证。", "支持组带着新成果回归，同伴追问其条件与依据。"],
    ["独立检验支持效果", 10, "个体独立活动", "个体", "学生—内容", "确认学生能否在无额外提示下完成进阶。", "独立完成同构任务并提交个人证据。"],
    ["汇总共性与个别后续", 5, "全班共同活动", "全班", "师生", "形成班级共同结论并明确后续支持。", "归纳共同进步与仍需个别支持的问题。"]
  ],
  parallel: [
    ["共同定向与分流说明", 6, "全班共同活动", "全班", "师生", "理解共同目标、分组依据与汇合标准。", "比较诊断证据并说明各组任务与共同成果格式。"],
    ["分层小组并行进阶", 15, "同质小组并行", "同质小组", "生生机", "各组完成与当前水平相邻的进阶目标。", "三个小组分别完成要素识别、关系建构和迁移挑战。"],
    ["跨组解释与成果互证", 10, "异质小组协同", "异质小组", "生生", "把不同层级成果整合为完整论证。", "重新组队，解释原组成果并回应跨组质疑。"],
    ["独立应用与再次分流", 9, "个体独立活动", "个体", "学生—内容", "独立应用关系结构并形成复诊证据。", "完成退出任务，依据结果决定巩固或挑战。"],
    ["共同汇聚学习成果", 5, "全班共同活动", "全班", "师生", "提炼各组共享的思维进阶。", "展示典型成果，归纳完整论证与迁移条件。"]
  ],
  stations: [
    ["共同定向与转站规则", 5, "全班共同活动", "全班", "师生", "明确各学习站功能、成果与转站条件。", "用诊断样例确定共同问题并说明站点选择规则。"],
    ["功能学习站同时运行", 22, "学习站轮转", "学习站", "生生机", "依据诊断需要在不同站点获得针对性进阶。", "教师支持站、关系建构站和迁移挑战站并行运行。"],
    ["站点成果交换互证", 8, "异质小组协同", "异质小组", "生生", "连接不同站点形成的要素、关系与证据。", "学生用站点成果互相解释并补全完整论证。"],
    ["独立完成出口任务", 7, "个体独立活动", "个体", "学生—内容", "检验站点学习能否迁移到个人作答。", "独立完成同构问题并提交出口卡。"],
    ["汇总与安排后续站点", 3, "全班共同活动", "全班", "师生", "确认达成情况并确定后续学习去向。", "快速回看证据，记录仍需进入支持站的学生。"]
  ],
  individual: [
    ["共同定向与个人目标确认", 6, "全班共同活动", "全班", "师生", "理解共同目标并确认个人进阶目标。", "比较诊断证据，选择本轮需要重点改进的目标。"],
    ["个体表达与首次尝试", 10, "个体独立活动", "个体", "生机", "完整表达当前理解并暴露关系缺口。", "独立完成任务，AI只围绕已有表达进行中性追问。"],
    ["按需获得分级支架", 10, "个体独立活动", "个体", "师生机", "根据证据获得适度支持并修正理解。", "系统推荐支架强度，教师确认后学生再次尝试。"],
    ["撤除支架再次验证", 11, "个体独立活动", "个体", "学生—内容", "在无方向性提示下独立达到进阶目标。", "完成新的同构问题并保留修改前后证据。"],
    ["同伴分享与全班提升", 8, "异质小组协同", "异质小组", "生生", "把个人进阶转化为可交流、可迁移的理解。", "分享个人关键修改，全班归纳完整论证结构。"]
  ]
};

function generatedImplementation(structure) {
  const copies = {
    "全班共同活动": "教师明确任务与成果标准，学生先独立判断，再进行全班交流；教师用学生证据组织追问。",
    "同质小组并行": "各组在同一时段完成不同任务，教师优先支持基础组；达到共同成果标准后汇合。",
    "学习站轮转": "各站同时开展，学生按诊断需要进入或转站；每站写明产出、时间与转站条件。",
    "异质小组协同": "学生先独立准备，再解释、质疑并修改观点；教师选择关键关系组织汇聚。",
    "个体独立活动": "学生独立完成并提交证据，教师不提供方向性提示，系统记录修改与达成情况。"
  };
  return copies[structure] || copies["全班共同活动"];
}

function buildGeneratedInterventionSequence(pathKey) {
  const blueprint = interventionSequenceBlueprints[pathKey] || interventionSequenceBlueprints.progressive;
  const activities = blueprint.map(([title, duration, structure, organization, dialogue, goal, task], index) => ({
    title, duration, structure, organization, dialogue, goal, task,
    implementation: generatedImplementation(structure),
    support: "支架与学生当前证据相匹配；达到标准后减弱或撤除，证据不足时先补充表达。",
    branches: structure === "同质小组并行" || structure === "学习站轮转" ? createDefaultActivityBranches(structure) : [],
    checkpoint: index === 0 || index === 2,
    checkpointQuestion: "学生是否呈现了本阶段目标要求的关键关系？",
    checkpointEvidence: "个人作答、解释记录和活动成果。",
    checkpointStandard: "依据本阶段可观察表现判断达到、未达到或证据不足。",
    reached: "进入下一阶段并适度撤除支架。",
    notReached: "保留必要支架并进行短时支持后再次尝试。",
    insufficient: "使用中性追问补充表达后再判断。"
  }));
  const selected = getSelectedFusionStrategies();
  if (selected.includes("embedded")) activities[1].support += " 为少数学生设置教师短时支持，达到标准后立即回归主任务。";
  if (selected.includes("parallel") && pathKey !== "stations") {
    activities[1].structure = "同质小组并行";
    activities[1].organization = "同质小组";
    activities[1].branches = createDefaultActivityBranches("同质小组并行");
  }
  if (selected.includes("collaborative")) {
    activities[2].structure = "异质小组协同";
    activities[2].organization = "异质小组";
    activities[2].dialogue = "生生机";
    activities[2].implementation += " 保留异质同伴解释和质疑环节。";
  }
  if (selected.includes("stations") && pathKey !== "parallel") {
    activities[1].structure = "学习站轮转";
    activities[1].organization = "学习站";
    activities[1].branches = createDefaultActivityBranches("学习站轮转");
  }
  if (selected.includes("individual")) {
    activities[3].structure = "个体独立活动";
    activities[3].organization = "个体";
    activities[3].dialogue = "生机";
    activities[3].support += " AI依据教师设定的边界提供分级支架。";
  }
  const targetMinutes = getDesiredInterventionDuration();
  const baseMinutes = activities.reduce((sum, activity) => sum + activity.duration, 0);
  let assignedMinutes = 0;
  activities.forEach((activity, index) => {
    if (index === activities.length - 1) activity.duration = Math.max(1, targetMinutes - assignedMinutes);
    else {
      activity.duration = Math.max(1, Math.round(activity.duration * targetMinutes / baseMinutes));
      assignedMinutes += activity.duration;
    }
  });
  return activities;
}

function markActivitySequencePending() {
  const source = document.getElementById("activitySequenceSource");
  source.textContent = "路径设置已变化，请重新生成活动";
  source.classList.add("pending");
  document.getElementById("generateActivitySequence").textContent = "重新生成活动序列";
}

function rebuildInterventionSequence() {
  const activities = buildGeneratedInterventionSequence(ui.selectedPrimaryPath);
  Object.keys(interventionActivities).forEach(key => delete interventionActivities[key]);
  const list = document.getElementById("sortableActivityList");
  list.replaceChildren();
  activities.forEach((activity, index) => {
    const id = String(index + 1);
    interventionActivities[id] = activity;
    const card = createInterventionActivityCard(id, index + 1);
    list.append(card);
    renderInlineActivityDetails(card, id);
  });
  ui.nextInterventionActivityId = activities.length + 1;
  renumberInterventionActivities();
  updateInterventionTimeTotal();
  const path = interventionPathContent[ui.selectedPrimaryPath];
  const fusionLabels = getSelectedFusionStrategies().map(key => interventionFusionLabels[key]);
  const source = document.getElementById("activitySequenceSource");
  source.textContent = `依据：${path.name}${fusionLabels.length ? `＋${fusionLabels.join("＋")}` : ""}`;
  source.classList.remove("pending");
}

function createInlineTextarea(labelCopy, fieldName, value, rows = 3, wide = false) {
  const label = document.createElement("label");
  if (wide) label.className = "wide";
  label.append(document.createTextNode(labelCopy));
  const field = document.createElement("textarea");
  field.rows = rows;
  field.value = value || "";
  field.dataset.inlineActivityField = fieldName;
  label.append(field);
  return label;
}

function createInlineSelect(labelCopy, fieldName, value, options) {
  const label = document.createElement("label");
  label.append(document.createTextNode(labelCopy));
  const select = document.createElement("select");
  select.dataset.inlineActivityField = fieldName;
  options.forEach(copy => {
    const option = document.createElement("option");
    option.textContent = copy;
    select.append(option);
  });
  select.value = value;
  label.append(select);
  return label;
}

function getProgressionGoalOptions() {
  return Array.from(document.querySelectorAll("#progressionGoalRows tr")).map((row, index) => {
    const fixedTitle = row.querySelector(".goal-level-title-input")?.value.trim();
    const levelSelect = row.querySelector(".goal-level-select");
    const customTitle = row.querySelector(".custom-goal-level-input")?.value.trim();
    const title = fixedTitle || (levelSelect?.value === "自定义进阶目标" ? customTitle : levelSelect?.value) || `进阶目标${index + 1}`;
    const content = row.cells[1]?.querySelector("textarea")?.value.trim() || "";
    return { title, content };
  }).filter(item => item.title || item.content);
}

function populateGoalTemplateSelect(select, selectedTitle = "") {
  select.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "选择前面设计的进阶目标";
  select.append(placeholder);
  getProgressionGoalOptions().forEach(({ title, content }) => {
    const option = document.createElement("option");
    option.value = title;
    option.textContent = title;
    option.dataset.goalText = content;
    select.append(option);
  });
  select.value = Array.from(select.options).some(option => option.value === selectedTitle) ? selectedTitle : "";
}

function createInlineGoalField(activity) {
  const label = document.createElement("label");
  label.className = "activity-goal-field";
  const heading = document.createElement("span");
  heading.className = "activity-goal-label-row";
  const title = document.createElement("b");
  title.textContent = "活动目标";
  const select = document.createElement("select");
  select.className = "activity-goal-template";
  select.dataset.inlineGoalTemplate = "";
  populateGoalTemplateSelect(select, activity.goalSource || "");
  heading.append(title, select);
  const field = document.createElement("textarea");
  field.rows = 3;
  field.value = activity.goal || "";
  field.dataset.inlineActivityField = "goal";
  label.append(heading, field);
  return label;
}

function renderInlineBranchRows(activity, body) {
  body.replaceChildren();
  activity.branches.forEach((branch, index) => {
    const row = document.createElement("tr");
    ["name", "target", "students", "task", "support"].forEach(fieldName => {
      const cell = document.createElement("td");
      cell.contentEditable = "true";
      cell.dataset.inlineBranchField = fieldName;
      cell.dataset.branchIndex = String(index);
      cell.textContent = branch[fieldName];
      row.append(cell);
    });
    const actionCell = document.createElement("td");
    const remove = document.createElement("button");
    remove.className = "text-button";
    remove.type = "button";
    remove.dataset.removeInlineBranch = String(index);
    remove.textContent = "删除";
    actionCell.append(remove);
    row.append(actionCell);
    body.append(row);
  });
}

function renderInlineActivityDetails(card, id) {
  const activity = interventionActivities[id];
  if (!activity || !card) return;
  const body = card.querySelector(".sequence-activity-body");
  body.replaceChildren();

  const editor = document.createElement("div");
  editor.className = "inline-activity-editor";
  editor.dataset.inlineActivityEditor = id;

  const tools = document.createElement("div");
  tools.className = "inline-activity-tools";
  const regenerate = document.createElement("button");
  regenerate.className = "button ai small";
  regenerate.type = "button";
  regenerate.dataset.regenerateInlineActivity = id;
  regenerate.textContent = "AI重新设计本活动";
  tools.append(regenerate);

  const grid = document.createElement("div");
  grid.className = "activity-editor-grid";
  grid.append(
    createInlineGoalField(activity),
    createInlineTextarea("任务与内容", "task", activity.task, 3),
    createInlineTextarea("教学实施", "implementation", activity.implementation, 3),
    createInlineTextarea("差异化支持", "support", activity.support, 3)
  );
  const settings = document.createElement("div");
  settings.className = "activity-settings-grid";
  settings.append(
    createInlineSelect("活动推进方式", "structure", activity.structure || "全班共同活动", ["全班共同活动", "同质小组并行", "学习站轮转", "异质小组协同", "个体独立活动"]),
    createInlineSelect("组织形式", "organization", activity.organization, ["全班", "同质小组", "异质小组", "个体", "学习站"]),
    createInlineSelect("会话关系", "dialogue", activity.dialogue, ["师生", "生生", "师生机", "生生机", "生机", "学生—内容"])
  );

  const branchEditor = document.createElement("section");
  branchEditor.className = "parallel-branch-editor inline-branch-editor";
  const branchHeader = document.createElement("header");
  const branchIntro = document.createElement("div");
  const branchTitle = document.createElement("b");
  branchTitle.textContent = activity.structure === "学习站轮转" ? "学习站设计" : "并行小组设计";
  const branchHint = document.createElement("span");
  branchHint.textContent = "同一课堂阶段内开展，分别设置目标、学生、任务和支架。";
  branchIntro.append(branchTitle, branchHint);
  const addBranch = document.createElement("button");
  addBranch.className = "button small";
  addBranch.type = "button";
  addBranch.dataset.addInlineBranch = id;
  addBranch.textContent = "＋增加分支";
  branchHeader.append(branchIntro, addBranch);
  const tableWrap = document.createElement("div");
  tableWrap.className = "parallel-branch-table-wrap";
  const table = document.createElement("table");
  const tableHead = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["小组／学习站", "对应进阶目标", "相关学生", "学习任务", "支架与资源", "操作"].forEach(copy => {
    const cell = document.createElement("th");
    cell.textContent = copy;
    headRow.append(cell);
  });
  tableHead.append(headRow);
  const tableBody = document.createElement("tbody");
  tableBody.className = "inline-branch-rows";
  if (!activity.branches?.length) activity.branches = createDefaultActivityBranches(activity.structure);
  renderInlineBranchRows(activity, tableBody);
  table.append(tableHead, tableBody);
  tableWrap.append(table);
  branchEditor.append(branchHeader, tableWrap);
  branchEditor.hidden = activity.structure !== "同质小组并行" && activity.structure !== "学习站轮转";

  const checkpoint = document.createElement("div");
  checkpoint.className = "embedded-checkpoint";
  const checkpointHeading = document.createElement("div");
  checkpointHeading.className = "checkpoint-heading";
  const checkpointLabel = document.createElement("label");
  const checkpointToggle = document.createElement("input");
  checkpointToggle.type = "checkbox";
  checkpointToggle.checked = activity.checkpoint;
  checkpointToggle.dataset.inlineCheckpoint = id;
  checkpointLabel.append(checkpointToggle, document.createTextNode("在本活动后设置形成性诊断"));
  const help = document.createElement("button");
  help.className = "help-dot";
  help.type = "button";
  help.dataset.interventionHelp = "checkpoint";
  help.setAttribute("aria-label", "查看形成性诊断说明");
  help.textContent = "?";
  checkpointHeading.append(checkpointLabel, help);
  const checkpointFields = document.createElement("div");
  checkpointFields.className = "checkpoint-fields inline-checkpoint-fields";
  checkpointFields.hidden = !activity.checkpoint;
  checkpointFields.append(
    createInlineTextarea("判断什么", "checkpointQuestion", activity.checkpointQuestion, 2),
    createInlineTextarea("收集什么证据", "checkpointEvidence", activity.checkpointEvidence, 2),
    createInlineTextarea("判断标准", "checkpointStandard", activity.checkpointStandard, 2)
  );
  const routes = document.createElement("div");
  routes.className = "checkpoint-routes";
  routes.append(
    createInlineTextarea("达到后", "reached", activity.reached, 2),
    createInlineTextarea("未达到后", "notReached", activity.notReached, 2),
    createInlineTextarea("证据不足", "insufficient", activity.insufficient, 2)
  );
  checkpointFields.append(routes);
  checkpoint.append(checkpointHeading, checkpointFields);

  editor.append(tools, grid, settings, branchEditor, checkpoint);
  body.append(editor);
}

function renderAllInlineActivityDetails() {
  document.querySelectorAll("[data-sequence-activity]").forEach(card => renderInlineActivityDetails(card, card.dataset.sequenceActivity));
}

function getInlineActivityContext(target) {
  const card = target.closest("[data-sequence-activity]");
  if (!card) return null;
  return { card, id: card.dataset.sequenceActivity, activity: interventionActivities[card.dataset.sequenceActivity] };
}

function saveInlineActivityField(target) {
  const context = getInlineActivityContext(target);
  if (!context?.activity) return;
  const fieldName = target.dataset.inlineActivityField;
  if (fieldName) context.activity[fieldName] = target.value;
  const branchField = target.dataset.inlineBranchField;
  if (branchField) {
    const branch = context.activity.branches[Number(target.dataset.branchIndex)];
    if (branch) branch[branchField] = target.textContent.trim();
  }
}

function refreshInlineActivityStructure(target) {
  const context = getInlineActivityContext(target);
  if (!context?.activity) return;
  const { activity, card } = context;
  const branchEditor = card.querySelector(".inline-branch-editor");
  const supportsBranches = activity.structure === "同质小组并行" || activity.structure === "学习站轮转";
  if (supportsBranches && !activity.branches?.length) activity.branches = createDefaultActivityBranches(activity.structure);
  branchEditor.hidden = !supportsBranches;
  branchEditor.querySelector("header b").textContent = activity.structure === "学习站轮转" ? "学习站设计" : "并行小组设计";
  if (supportsBranches) renderInlineBranchRows(activity, branchEditor.querySelector(".inline-branch-rows"));
  const meta = card.querySelector(".sequence-main small");
  if (meta.firstChild) meta.firstChild.textContent = `${activity.structure} · ${activity.organization} `;
}

function saveActiveInterventionActivity() {
  const activity = interventionActivities[ui.activeInterventionActivity];
  if (!activity) return;
  activity.goal = document.getElementById("activityGoalField").value;
  activity.task = document.getElementById("activityTaskField").value;
  activity.implementation = document.getElementById("activityImplementationField").value;
  activity.support = document.getElementById("activitySupportField").value;
  activity.structure = document.getElementById("activityStructureField").value;
  activity.organization = document.getElementById("activityOrganizationField").value;
  activity.dialogue = document.getElementById("activityDialogueField").value;
  activity.goalLevels = Array.from(document.querySelectorAll("[data-activity-goal-level]:checked")).map(input => input.dataset.activityGoalLevel);
  activity.checkpoint = document.getElementById("activityCheckpointEnabled").checked;
  activity.checkpointQuestion = document.getElementById("checkpointQuestionField").value;
  activity.checkpointEvidence = document.getElementById("checkpointEvidenceField").value;
  activity.checkpointStandard = document.getElementById("checkpointStandardField").value;
  activity.reached = document.getElementById("checkpointReachedField").value;
  activity.notReached = document.getElementById("checkpointNotReachedField").value;
  activity.insufficient = document.getElementById("checkpointInsufficientField").value;
  const card = document.querySelector(`[data-sequence-activity="${ui.activeInterventionActivity}"]`);
  if (card) {
    card.querySelector(".sequence-main small").firstChild.textContent = `${activity.structure} · ${activity.organization} `;
  }
}

function updateParallelBranchVisibility() {
  const structure = document.getElementById("activityStructureField").value;
  const branchEditor = document.getElementById("parallelBranchEditor");
  const supportsBranches = structure === "同质小组并行" || structure === "学习站轮转";
  branchEditor.hidden = !supportsBranches;
  document.getElementById("parallelBranchTitle").textContent = structure === "学习站轮转" ? "学习站设计" : "并行小组设计";
}

function renderInterventionActivityEditor(id) {
  const activity = interventionActivities[id];
  if (!activity) return;
  const editor = document.getElementById("activityDetailEditor");
  if (editor.dataset.activityInitialized === "true" && interventionActivities[ui.activeInterventionActivity]) saveActiveInterventionActivity();
  ui.activeInterventionActivity = id;
  editor.hidden = false;
  document.getElementById("activityEditorTitle").textContent = `活动：${activity.title}`;
  document.getElementById("activityGoalField").value = activity.goal;
  document.getElementById("activityTaskField").value = activity.task;
  document.getElementById("activityImplementationField").value = activity.implementation;
  document.getElementById("activitySupportField").value = activity.support;
  document.getElementById("activityStructureField").value = activity.structure || "全班共同活动";
  document.getElementById("activityOrganizationField").value = activity.organization;
  document.getElementById("activityDialogueField").value = activity.dialogue;
  document.querySelectorAll("[data-activity-goal-level]").forEach(input => {
    input.checked = (activity.goalLevels || []).includes(input.dataset.activityGoalLevel);
  });
  document.getElementById("activityCheckpointEnabled").checked = activity.checkpoint;
  document.getElementById("checkpointQuestionField").value = activity.checkpointQuestion;
  document.getElementById("checkpointEvidenceField").value = activity.checkpointEvidence;
  document.getElementById("checkpointStandardField").value = activity.checkpointStandard;
  document.getElementById("checkpointReachedField").value = activity.reached;
  document.getElementById("checkpointNotReachedField").value = activity.notReached;
  document.getElementById("checkpointInsufficientField").value = activity.insufficient;
  document.getElementById("checkpointFields").hidden = !activity.checkpoint;
  updateParallelBranchVisibility();
  document.querySelectorAll("[data-sequence-activity]").forEach(card => card.classList.toggle("active", card.dataset.sequenceActivity === id));
  editor.dataset.activityInitialized = "true";
}

function renumberInterventionActivities() {
  document.querySelectorAll("[data-sequence-activity]").forEach((card, index) => {
    const number = index + 1;
    card.querySelector(".sequence-number").textContent = number;
    const dragButton = card.querySelector(".drag-handle");
    const upButton = card.querySelector('[data-move-activity="up"]');
    const downButton = card.querySelector('[data-move-activity="down"]');
    dragButton.setAttribute("aria-label", `拖拽阶段${number}排序`);
    upButton.setAttribute("aria-label", `上移阶段${number}`);
    downButton.setAttribute("aria-label", `下移阶段${number}`);
    upButton.disabled = number === 1;
    downButton.disabled = number === document.querySelectorAll("[data-sequence-activity]").length;
  });
  document.getElementById("activityOrderStatus").textContent = "课堂阶段顺序已自动保存";
}

function moveInterventionActivity(card, direction) {
  const sibling = direction === "up" ? card.previousElementSibling : card.nextElementSibling;
  if (!sibling) return;
  if (direction === "up") card.parentElement.insertBefore(card, sibling);
  else card.parentElement.insertBefore(sibling, card);
  renumberInterventionActivities();
}

function createInterventionActivityCard(id, number) {
  const activity = interventionActivities[id] || {};
  const card = document.createElement("article");
  card.className = "sequence-activity expanded";
  card.draggable = true;
  card.dataset.sequenceActivity = id;

  const header = document.createElement("header");
  header.className = "sequence-activity-header";

  const dragButton = document.createElement("button");
  dragButton.className = "drag-handle";
  dragButton.type = "button";
  dragButton.textContent = "⠿";
  const sequenceNumber = document.createElement("span");
  sequenceNumber.className = "sequence-number";
  sequenceNumber.textContent = number;
  const main = document.createElement("div");
  main.className = "sequence-main";
  const meta = document.createElement("small");
  meta.textContent = `${activity.structure || "全班共同活动"} · ${activity.organization || "待设置"}`;
  if (activity.checkpoint) {
    const checkpointTag = document.createElement("em");
    checkpointTag.className = "checkpoint-tag";
    checkpointTag.textContent = "检查点";
    meta.append(" ", checkpointTag);
  }
  const title = document.createElement("b");
  title.textContent = activity.title || "新学习活动";
  main.append(meta, title);
  const minuteLabel = document.createElement("label");
  const minuteInput = document.createElement("input");
  minuteInput.className = "intervention-minute-input";
  minuteInput.type = "number";
  minuteInput.value = String(activity.duration || 5);
  minuteInput.min = "1";
  minuteLabel.append(minuteInput, "分钟");
  const actions = document.createElement("div");
  actions.className = "sequence-actions";
  [["up", "↑"], ["down", "↓"]].forEach(([direction, copy]) => {
    const button = document.createElement("button");
    button.className = "text-button";
    button.type = "button";
    button.dataset.moveActivity = direction;
    button.textContent = copy;
    actions.append(button);
  });
  const toggle = document.createElement("button");
  toggle.className = "button small quiet";
  toggle.type = "button";
  toggle.dataset.toggleSequenceActivity = "";
  toggle.textContent = "收起";
  actions.append(toggle);
  header.append(dragButton, sequenceNumber, main, minuteLabel, actions);

  const body = document.createElement("div");
  body.className = "sequence-activity-body";
  card.append(header, body);
  return card;
}

function addInterventionActivity() {
  const id = String(ui.nextInterventionActivityId++);
  interventionActivities[id] = {
    title: "新学习活动",
    structure: "全班共同活动",
    goalLevels: [],
    branches: [],
    goal: "",
    task: "",
    implementation: "",
    support: "",
    organization: "全班",
    dialogue: "师生",
    checkpoint: false,
    checkpointQuestion: "",
    checkpointEvidence: "",
    checkpointStandard: "",
    reached: "",
    notReached: "",
    insufficient: ""
  };
  const list = document.getElementById("sortableActivityList");
  const card = createInterventionActivityCard(id, list.children.length + 1);
  list.append(card);
  renderInlineActivityDetails(card, id);
  renumberInterventionActivities();
  updateInterventionTimeTotal();
  card.scrollIntoView({ behavior: "smooth", block: "center" });
}

function addProgressionGoalRow() {
  const row = document.createElement("tr");
  row.dataset.goalLevel = "custom";
  const levelCell = document.createElement("td");
  levelCell.className = "editable-goal-level";
  const levelSelect = document.createElement("select");
  levelSelect.className = "goal-level-select";
  ["M 多点结构目标", "R 关联结构目标", "EA 拓展抽象目标", "自定义进阶目标"].forEach(copy => {
    const option = document.createElement("option");
    option.textContent = copy;
    levelSelect.append(option);
  });
  const customInput = document.createElement("input");
  customInput.className = "custom-goal-level-input";
  customInput.placeholder = "输入自定义目标层级名称";
  customInput.setAttribute("aria-label", "自定义目标层级名称");
  customInput.hidden = true;
  const audienceInput = document.createElement("input");
  audienceInput.className = "goal-level-audience-input";
  audienceInput.placeholder = "填写适用对象";
  audienceInput.setAttribute("aria-label", "目标适用对象");
  levelCell.append(levelSelect, customInput, audienceInput);
  const placeholders = ["填写基于共同核心目标形成的具体目标", "填写相关学生姓名", "填写课堂中可观察的达成表现"];
  const editableCells = placeholders.map(placeholder => {
    const cell = document.createElement("td");
    const field = document.createElement("textarea");
    field.rows = 4;
    field.placeholder = placeholder;
    cell.append(field);
    return cell;
  });
  const actionCell = document.createElement("td");
  const remove = document.createElement("button");
  remove.className = "text-button";
  remove.type = "button";
  remove.dataset.removeProgressionGoal = "";
  remove.textContent = "删除";
  actionCell.append(remove);
  row.append(levelCell, ...editableCells, actionCell);
  document.getElementById("progressionGoalRows").append(row);
  row.scrollIntoView({ behavior: "smooth", block: "center" });
}

function addParallelBranchRow() {
  const row = document.createElement("tr");
  ["新小组／学习站", "选择或填写进阶目标", "填写学生姓名或分组条件", "填写学习任务", "填写支架与资源"].forEach(copy => {
    const cell = document.createElement("td");
    cell.contentEditable = "true";
    cell.textContent = copy;
    row.append(cell);
  });
  const actionCell = document.createElement("td");
  const remove = document.createElement("button");
  remove.className = "text-button";
  remove.type = "button";
  remove.dataset.removeParallelBranch = "";
  remove.textContent = "删除";
  actionCell.append(remove);
  row.append(actionCell);
  document.getElementById("parallelBranchRows").append(row);
}

function safeReportHref(value) {
  if (!value) return null;
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch (_) { return null; }
}

function buildInterventionReport(report) {
  const result = report.result;
  const plan = result.integrated_plan;
  const basic = plan.basic_information;
  const diagnosis = plan.diagnosis_summary;
  const context = plan.teaching_context_and_conditions;
  const goals = plan.goal_design;
  const cell = value => escapeHTML(Array.isArray(value) ? value.join("；") : value == null ? "—" : String(value));
  const table = (heads, rows, className = "") => `<div class="report-table-wrap"><table class="report-table ${className}"><thead><tr>${heads.map(head => `<th>${escapeHTML(head)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(value => `<td>${cell(value)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  const task = diagnosis.diagnostic_task;
  const sourceHref = safeReportHref(task.source_ref);
  const taskSource = sourceHref ? `<a href="${escapeHTML(sourceHref)}" target="_blank" rel="noopener noreferrer">查看诊断任务来源</a>` : "";
  const basicTable = `<div class="report-table-wrap"><table class="report-table report-basic-table"><tbody>
    <tr><th>学科</th><td>${cell(basic.subject)}</td><th>年级</th><td>${cell(basic.grade)}</td></tr>
    <tr><th>教材版本与章节</th><td>${cell(basic.textbook_version_and_chapter)}</td><th>班级</th><td>${cell(basic.class_name)}</td></tr>
    <tr><th>精准教学主题</th><td>${cell(basic.precision_teaching_topic)}</td><th>教师</th><td>${cell(basic.teacher_name)}</td></tr>
    <tr><th>精准教学目标</th><td>${cell(basic.precision_teaching_goals)}</td><th>精准教学内容</th><td>${cell(basic.precision_teaching_content)}</td></tr>
  </tbody></table></div>`;
  const activityCards = plan.activities.map((activity, index) => `<section class="report-activity">
    <h4>活动${index + 1}：${cell(activity.activity_name)}（${Number(activity.duration_minutes)}分钟；${cell(activity.organization_forms.join("、"))}）</h4>
    <div class="report-table-wrap"><table class="report-table report-activity-table"><tbody>
      <tr><th>活动目标</th><td colspan="3">${cell(activity.activity_objective)}</td></tr>
      <tr><th rowspan="2">活动过程</th><th>教师活动</th><th>学生活动</th><th>AI辅助</th></tr>
      <tr><td>${cell(activity.teacher_activities)}</td><td>${cell(activity.student_activities)}</td><td>${activity.ai_support ? cell(activity.ai_support) : ""}</td></tr>
      <tr><th>学习材料与资源</th><td colspan="3">${cell(activity.learning_materials_and_resources)}</td></tr>
      <tr><th>学习产出</th><td colspan="3">${cell(activity.learning_product)}</td></tr>
    </tbody></table></div></section>`).join("");
  const reviews = result.audit_summary.review_items;
  const statusClass = { ready_for_use: "success", ready_with_suggestions: "warning", needs_revision: "danger" }[result.integration_status] || "neutral";
  return `<header class="report-cover"><span>精准干预方案</span><h2>${cell(basic.plan_title)}</h2><p>${cell(basic.class_name)} · ${cell(basic.subject)} · ${Number(basic.total_duration_minutes)} 分钟</p><em class="status ${statusClass}">${cell(result.audit_summary.overall_conclusion)}</em></header>
    <section><h3>1. 精准教学基本信息</h3>${basicTable}</section>
    <section><h3>2. 学生诊断结果</h3><h4>诊断任务</h4><div class="report-task-block"><p>${cell(task.task_text)}</p>${taskSource}</div>
      ${table(["SOLO层级", "人数及比例", "典型表现", "主要进阶障碍"], diagnosis.distribution_rows.map(row => [`${row.level} ${row.level_name}`, `${row.count} 人 · ${Math.round(row.proportion * 100)}%`, row.typical_performance || "—", row.main_obstacles || "—"]), "report-diagnosis-table")}
      <p class="report-summary"><b>班级诊断总述：</b>${cell(diagnosis.overall_summary.teacher_facing_paragraph)}</p></section>
    <section><h3>3. 教学情境与条件</h3>${table(["项目", "教师填写内容"], [
      ["教学内容与课标分析", context.teaching_content_and_curriculum_analysis],
      ["教学重点与难点分析", context.teaching_focus_and_difficulty_analysis],
      ["教学时长", `${context.planned_duration_minutes} 分钟`],
      ["教学环境与 AI 支持条件", context.teaching_environment_and_ai_support_conditions]
    ], "report-context-table")}</section>
    <section><h3>4. 精准干预目标</h3><h4>共同核心目标</h4><p>${cell(goals.common_core_goal.goal_statement)}</p>
      ${table(["目标认知结构与理由", "可观察的达成表现"], [[goals.common_core_goal.structure_and_rationale, goals.common_core_goal.observable_achievement]])}
      <h4>分层进阶目标</h4>${table(["目标层级", "面向学生", "具体目标内容", "可观察的达成表现"], goals.progression_goals.map(goal => [goal.target_level, goal.students_display, goal.goal_statement, goal.observable_achievement]), "report-progression-table")}
      <p class="report-note"><b>说明：</b>${cell(goals.progression_goal_note)}</p></section>
    <section><h3>5. 学习活动设计</h3>${activityCards}</section>
    <section><h3>6. 形成性评价</h3>${table(["评价时机", "评价目标", "学习证据与判断标准", "后续调整", "判断主体"], plan.formative_evaluations.map(item => [item.timing, item.evaluation_goal, item.evidence_and_criteria, item.adjustment, item.decision_by]), "report-evaluation-table")}</section>
    <section><h3>7. 方案审核结果</h3>${table(["检查项目", "状态", "发现的问题", "修改建议"], reviews.map(item => [item.check_item, item.status, item.issue, item.suggestion]), "report-review-table")}
      <p class="report-summary"><b>审核结论：</b>${cell(result.audit_summary.overall_conclusion)}</p></section>`;
}

function showIntegrationReport(payload) {
  const target = document.getElementById("interventionReportContent");
  const status = document.getElementById("integrationReportStatus");
  const generate = document.getElementById("refreshInterventionReport");
  const actions = document.getElementById("integrationReportActions");
  const report = payload.report;
  serverState.integrationReport = report;
  generate.disabled = !payload.ready;
  generate.hidden = !report;
  generate.textContent = "AI重新生成教学报告";
  actions.hidden = !report;
  const exportReady = report?.status === "current";
  document.getElementById("downloadInterventionPdf").disabled = !exportReady;
  document.getElementById("downloadInterventionReport").disabled = !exportReady;
  if (!report) {
    status.hidden = Boolean(payload.ready);
    status.textContent = payload.ready ? "" : payload.prerequisite || "上游资料尚未齐备。";
    target.innerHTML = `<div class="stage-empty-state"><span>教学报告与方案审核</span><h2>尚未生成</h2><button class="button primary" id="refreshInterventionReportEmpty" ${payload.ready ? "" : "disabled"}>AI生成教学报告</button></div>`;
    return;
  }
  status.hidden = false;
  status.textContent = exportReady
    ? `报告已生成 · ${report.result.audit_summary.overall_conclusion}`
    : `上游资料已变化，旧报告仅供对照。${payload.prerequisite || "请重新生成后再导出。"}`;
  target.innerHTML = buildInterventionReport(report);
}

async function renderInterventionReport() {
  const teachingId = serverState?.current_teaching_id;
  const classroomId = ui.activeInterventionClassroomId;
  const target = document.getElementById("interventionReportContent");
  serverState.integrationReport = null;
  document.getElementById("integrationReportActions").hidden = true;
  document.getElementById("refreshInterventionReport").hidden = true;
  document.getElementById("integrationReportStatus").hidden = false;
  document.getElementById("integrationReportStatus").textContent = "正在读取报告状态…";
  document.getElementById("downloadInterventionPdf").disabled = true;
  document.getElementById("downloadInterventionReport").disabled = true;
  target.innerHTML = '<p class="report-empty">正在读取已保存的教学报告…</p>';
  try {
    const payload = await window.PlatformAPI.getIntegrationReport(teachingId, classroomId);
    if (serverState?.current_teaching_id !== teachingId || ui.activeInterventionClassroomId !== classroomId) return;
    showIntegrationReport(payload);
  } catch (error) {
    document.getElementById("integrationReportStatus").textContent = `报告读取失败：${error.message}`;
    target.innerHTML = `<p class="report-empty">${escapeHTML(error.message)}</p>`;
  }
}

function downloadInterventionReport() {
  const content = document.getElementById("interventionReportContent").innerHTML;
  const title = getTeachingById(serverState.current_teaching_id)?.title || "精准干预教学报告";
  const style = `body{font-family:Arial,"Microsoft YaHei",sans-serif;color:#1d2d2e;line-height:1.65;max-width:900px;margin:40px auto;padding:0 24px}h2,h3,h4{color:#174f4a}section{border-top:1px solid #d8e5e3;padding:18px 0}p{white-space:pre-wrap}table{border-collapse:collapse;width:100%}td,th{border:1px solid #d8e5e3;padding:8px}.report-context-table{table-layout:fixed}.report-context-table th:first-child,.report-context-table td:first-child{width:23%}.report-action-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.report-stage-list,.report-levels{display:flex;gap:12px;flex-wrap:wrap}.report-stage-list span,.report-levels span{padding:5px 9px;background:#eef7f5;border-radius:5px}.report-activity,.report-evaluations article{border:1px solid #d8e5e3;border-radius:8px;padding:15px;margin:12px 0}@media print{body{margin:0;max-width:none}}`;
  const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>${escapeHTML(title)}｜教学报告</title><style>${style}</style></head><body>${content}</body></html>`;
  const url = URL.createObjectURL(new Blob([html], {type: "text/html;charset=utf-8"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = `${title.replace(/[\\/:*?"<>|]/g, "-")}-教学报告.html`;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10000);
  showToast("已开始下载 HTML 教学报告");
}

async function downloadInterventionPdf(button) {
  const teachingId = serverState.current_teaching_id;
  const classroomId = ui.activeInterventionClassroomId;
  button.disabled = true;
  button.textContent = "正在生成 PDF…";
  try {
    const blob = await window.PlatformAPI.downloadIntegrationReportPdf(teachingId, classroomId);
    const title = getTeachingById(teachingId)?.title || "精准干预教学报告";
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${title.replace(/[\\/:*?"<>|]/g, "-")}-教学报告.pdf`;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 10000);
    showToast("已开始下载 PDF 教学报告");
  } catch (error) {
    showToast(`PDF 导出失败：${error.message}`);
  } finally {
    button.textContent = "下载 PDF";
    button.disabled = serverState?.integrationReport?.status !== "current";
  }
}

function showInterventionPlanPreview() {
  const design = serverState?.goalPath?.design;
  const legacyCompleted = !design && serverState?.current_workspace?.intervention?.status === "completed";
  if (design?.status !== "teacher_confirmed" && !legacyCompleted) {
    showToast("请先生成并确认目标与活动路径");
    return;
  }
  if ((serverState?.activityFormative?.design?.status !== "teacher_confirmed" || ui.activityFormativeDirty) && !legacyCompleted) {
    showToast("请先确认学习活动与形成性评价");
    return;
  }
  setInterventionStep(4);
  window.setTimeout(() => document.getElementById("interventionPlanPreview").scrollIntoView({ behavior: "smooth", block: "start" }), 80);
}

document.addEventListener("click", async event => {
  // These controls have their own listeners; do not report them as placeholders.
  if (event.target.closest("#teacherAuthScreen, #teacherLogoutButton, #addClassroomButton, .add-classroom-form, .edit-classroom-form")) return;
  const editClassroomButton = event.target.closest("[data-edit-classroom]");
  if (editClassroomButton) {
    const classroom = serverState.classrooms.find(item => item.id === editClassroomButton.dataset.editClassroom);
    if (!classroom) return;
    const card = editClassroomButton.closest(".class-profile");
    const form = document.createElement("form");
    form.className = "edit-classroom-form";
    form.innerHTML = `<label>学生人数<input name="student_count" type="number" min="0" max="1000" required></label>
      <label>班级背景（可选）<textarea name="background" rows="4" maxlength="2000"></textarea></label>
      <div class="classroom-form-actions"><button class="button primary" type="submit">保存班级信息</button><button class="button" type="button" data-cancel>取消</button></div>`;
    form.querySelector('[name="student_count"]').value = classroom.student_count;
    form.querySelector('[name="background"]').value = classroom.background || "";
    editClassroomButton.hidden = true;
    card.append(form);
    form.querySelector("[data-cancel]").addEventListener("click", () => { form.remove(); editClassroomButton.hidden = false; });
    form.addEventListener("submit", async submitEvent => {
      submitEvent.preventDefault();
      const saveButton = form.querySelector('[type="submit"]');
      saveButton.disabled = true;
      try {
        const updated = await window.PlatformAPI.updateClassroom(classroom.id, {
          student_count: Number(form.querySelector('[name="student_count"]').value),
          background: form.querySelector('[name="background"]').value.trim()
        });
        serverState.classrooms = serverState.classrooms.map(item => item.id === updated.id ? updated : item);
        renderClassrooms(serverState.classrooms);
        showToast("班级信息已保存");
      } catch (error) {
        showToast(`保存班级失败：${error.message}`);
        saveButton.disabled = false;
      }
    });
    form.querySelector('[name="student_count"]').focus();
    return;
  }
  if (event.target.closest("#generateActivityFormative, #generateActivityFormativeEmpty")) {
    const button = event.target.closest("#generateActivityFormative, #generateActivityFormativeEmpty");
    const teachingId = serverState.current_teaching_id;
    const classroomId = ui.activeInterventionClassroomId;
    ui.activityFormativeError = null;
    document.getElementById("activityFormativeError").hidden = true;
    document.getElementById("activityFormativeStatus").textContent = "生成中";
    button.disabled = true;
    button.textContent = "正在生成…";
    try {
      const payload = await window.PlatformAPI.generateActivityFormative(
        teachingId, classroomId, activityRequirementsInput()
      );
      if (serverState.current_teaching_id !== teachingId || ui.activeInterventionClassroomId !== classroomId) return;
      serverState.activityFormative = { goal_path_ready: true, source_summary: serverState?.activityFormative?.source_summary, design: payload.design };
      renderActivityFormative(serverState.activityFormative);
      showToast("学习活动与形成性评价已生成，请核对后确认");
    } catch (error) {
      if (serverState.current_teaching_id !== teachingId || ui.activeInterventionClassroomId !== classroomId) return;
      ui.activityFormativeError = error.message.replace(/^学习活动生成失败：/, "");
      renderActivityFormative(serverState.activityFormative);
      showToast(`学习活动生成失败：${ui.activityFormativeError}`);
    } finally {
      if (button.isConnected) {
        button.disabled = !serverState?.activityFormative?.goal_path_ready;
        button.textContent = button.id === "generateActivityFormativeEmpty"
          ? (ui.activityFormativeError ? "重新生成学习活动" : "AI生成学习活动")
          : "AI重新生成学习活动";
      }
    }
    return;
  }

  if (event.target.closest("#saveActivityFormativeDraft, #confirmActivityFormative")) {
    const button = event.target.closest("#saveActivityFormativeDraft, #confirmActivityFormative");
    const confirmed = button.id === "confirmActivityFormative";
    button.disabled = true;
    try {
      const payload = await window.PlatformAPI.saveActivityFormative(
        serverState.current_teaching_id, ui.activeInterventionClassroomId,
        collectActivityFormativeEdits(), confirmed, collectActivityPresentationOverrides()
      );
      serverState.activityFormative.design = payload.design;
      renderActivityFormative(serverState.activityFormative);
      showToast(confirmed ? "学习活动与形成性评价已确认" : "活动修改已保存，仍待教师确认");
    } catch (error) {
      showToast(`保存学习活动失败：${error.message}`);
      button.disabled = false;
    }
    return;
  }

  if (event.target.closest("#nextFromActivityFormative")) {
    setInterventionStep(4);
    return;
  }

  if (event.target.closest("#generateTeacherAnalysis")) {
    const button = event.target.closest("#generateTeacherAnalysis");
    if (!ui.activeInterventionClassroomId) {
      showToast("请先选择班级");
      return;
    }
    button.disabled = true;
    button.textContent = "正在生成…";
    try {
      const payload = await window.PlatformAPI.generateTeacherAnalysis(
        serverState.current_teaching_id, ui.activeInterventionClassroomId
      );
      const analysis = payload.teacher_analysis;
      document.getElementById("teachingContentAnalysis").value = analysis.content_analysis;
      document.getElementById("teachingFocusAnalysis").value = analysis.focus_analysis;
      const note = document.getElementById("teacherAnalysisSourceNote");
      note.textContent = analysis.source_note;
      note.hidden = false;
      if (serverState.goalPath) serverState.goalPath.teacher_analysis = analysis;
      showToast("教学分析草稿已生成，可修改后保存");
    } catch (error) {
      showToast(`教学分析生成失败：${error.message}`);
    } finally {
      button.disabled = !serverState?.goalPath?.class_diagnosis_ready;
      button.textContent = "AI重新生成教学分析";
    }
    return;
  }

  if (event.target.closest("#saveTeacherAnalysisAndNext")) {
    const input = {
      content_analysis: document.getElementById("teachingContentAnalysis").value.trim(),
      focus_analysis: document.getElementById("teachingFocusAnalysis").value.trim(),
      content_confirmed: true,
      focus_confirmed: true
    };
    if (!input.content_analysis || !input.focus_analysis) {
      showToast("请先生成或填写两项教学分析");
      return;
    }
    if (!document.getElementById("teachingEnvironmentConditions").value.trim()) {
      showToast("请填写课堂与技术条件");
      return;
    }
    const button = event.target.closest("#saveTeacherAnalysisAndNext");
    button.disabled = true;
    try {
      await window.PlatformAPI.saveTeacherAnalysis(
        serverState.current_teaching_id, ui.activeInterventionClassroomId, input
      );
      await loadGoalPath(serverState.current_teaching_id, ui.activeInterventionClassroomId);
      setInterventionStep(2);
    } catch (error) {
      showToast(`保存教学分析失败：${error.message}`);
    } finally {
      button.disabled = false;
    }
    return;
  }

  if (event.target.closest("#generateGoalPath, #generateGoalPathEmpty")) {
    const button = event.target.closest("#generateGoalPath, #generateGoalPathEmpty");
    const input = goalPathTeacherInput();
    const context = input.teacher_instructional_context;
    if (!ui.activeInterventionClassroomId) {
      showToast("请先选择已发布诊断任务的班级");
      return;
    }
    if (!context.teaching_content_and_curriculum_analysis || !context.teaching_focus_and_difficulty_analysis) {
      showToast("请先在“设计依据”填写并保存两项教学分析");
      setInterventionStep(1);
      return;
    }
    if (!context.teaching_environment_and_ai_support_conditions) {
      showToast("请先在“设计依据”填写“课堂与技术条件”，再保存分析");
      setInterventionStep(1);
      document.getElementById("teachingEnvironmentConditions").focus();
      return;
    }
    if (!Number.isInteger(context.planned_duration_minutes) || context.planned_duration_minutes < 10 || context.planned_duration_minutes > 300) {
      showToast("请在“设计依据”填写 10–300 分钟的教学时间");
      setInterventionStep(1);
      document.getElementById("interventionDurationMinutes").focus();
      return;
    }
    const savedAnalysis = serverState?.goalPath?.teacher_analysis;
    if (!savedAnalysis || savedAnalysis.content_analysis !== context.teaching_content_and_curriculum_analysis || savedAnalysis.focus_analysis !== context.teaching_focus_and_difficulty_analysis) {
      showToast("请先在“设计依据”保存当前两项教学分析，再生成目标与路径");
      setInterventionStep(1);
      return;
    }
    button.disabled = true;
    button.textContent = "正在生成…";
    try {
      const payload = await window.PlatformAPI.generateGoalPath(
        serverState.current_teaching_id, ui.activeInterventionClassroomId, input
      );
      serverState.goalPath = { ...serverState.goalPath, class_diagnosis_ready: true, design: payload.design };
      ui.goalPathAppliedKey = null;
      renderGoalPath(serverState.goalPath);
      showToast("目标与课堂路径已生成，请核对后确认");
    } catch (error) {
      showToast(`生成失败：${error.message}`);
    } finally {
      if (button.isConnected) {
        button.disabled = !serverState?.goalPath?.class_diagnosis_ready;
        button.textContent = button.id === "generateGoalPathEmpty" ? "AI生成目标与路径" : "AI重新生成目标与路径";
      }
    }
    return;
  }

  if (event.target.closest("[data-gp-toggle-edit]")) {
    const button = event.target.closest("[data-gp-toggle-edit]");
    const section = button.closest("section");
    const display = section.querySelector("[data-gp-path-readonly]");
    const editor = section.querySelector("[data-gp-path-editor]");
    if (editor.hidden) {
      display.hidden = true;
      editor.hidden = false;
      button.textContent = "完成修改";
      editor.querySelector("textarea, select, input")?.focus();
    } else {
      const edited = collectGoalPathEdits();
      display.querySelector("tbody").innerHTML = goalPathReadOnlyRows(edited.intervention_path);
      editor.hidden = true;
      display.hidden = false;
      button.textContent = "修改";
    }
    return;
  }

  if (event.target.closest("#saveGoalPathDraft, #confirmGoalPath")) {
    const button = event.target.closest("#saveGoalPathDraft, #confirmGoalPath");
    const confirmed = button.id === "confirmGoalPath";
    const result = collectGoalPathEdits();
    const editError = goalPathEditError(result);
    if (editError) {
      showToast(editError);
      return;
    }
    button.disabled = true;
    try {
      const payload = await window.PlatformAPI.saveGoalPath(
        serverState.current_teaching_id, ui.activeInterventionClassroomId, result, confirmed
      );
      serverState.goalPath.design = payload.design;
      ui.goalPathAppliedKey = null;
      renderGoalPath(serverState.goalPath);
      showToast(confirmed ? "目标与路径已由教师确认" : "修改已保存，仍待教师确认");
    } catch (error) {
      showToast(`保存失败：${error.message}`);
      button.disabled = false;
    }
    return;
  }

  if (event.target.closest("#nextFromGoalPath")) {
    setInterventionStep(3);
    return;
  }

  if (event.target.closest("#retryClassReport")) {
    const button = event.target.closest("#retryClassReport");
    button.disabled = true;
    try {
      await window.PlatformAPI.regenerateClassReport(serverState.current_teaching_id, ui.activeClassroomId);
      await loadStudentResults(serverState.current_teaching_id);
      showToast("班级报告已加入生成队列");
    } catch (error) {
      button.disabled = false;
      showToast(`班级报告重试失败：${error.message}`);
    }
    return;
  }
  if (!event.target.closest(".help-popover, [data-help]")) {
    closeHelpPopovers();
  }

  const helpButton = event.target.closest("[data-help]");
  if (helpButton) {
    const popover = document.getElementById(helpButton.dataset.help);
    const shouldOpen = popover.hidden;
    closeHelpPopovers();
    popover.hidden = !shouldOpen;
    helpButton.setAttribute("aria-expanded", String(shouldOpen));
    return;
  }

  const taskGuideButton = event.target.closest("[data-open-task-guide]");
  if (taskGuideButton) {
    openTaskGuide(taskGuideButton.dataset.openTaskGuide);
    return;
  }

  if (event.target.closest("[data-close-task-guide]")) {
    closeTaskGuide();
    return;
  }

  const interventionHelpButton = event.target.closest("[data-intervention-help]");
  if (interventionHelpButton) {
    openInterventionHelp(interventionHelpButton.dataset.interventionHelp);
    return;
  }

  if (event.target.closest("[data-close-intervention-help]")) {
    closeInterventionHelp();
    return;
  }

  const deleteTeachingButton = event.target.closest("[data-delete-teaching]");
  if (deleteTeachingButton) {
    const teachingId = deleteTeachingButton.dataset.deleteTeaching;
    const teachingTitle = deleteTeachingButton.dataset.teachingTitle;
    if (!window.confirm(`确认删除“${teachingTitle}”吗？删除后无法恢复。`)) return;
    deleteTeachingButton.disabled = true;
    deleteTeachingButton.textContent = "正在删除…";
    try {
      const result = await window.PlatformAPI.deleteTeaching(teachingId);
      serverState.precision_teachings = serverState.precision_teachings.filter(teaching => teaching.id !== teachingId);
      serverState.current_teaching_id = result.current_teaching_id;
      serverState.current_workspace = result.current_teaching_id
        ? await window.PlatformAPI.getWorkspace(result.current_teaching_id)
        : null;
      if (serverState.current_workspace) {
        serverState.precision_teachings = serverState.precision_teachings.map(item => item.id === result.current_teaching_id ? serverState.current_workspace.teaching : item);
        applyWorkspace(serverState.current_workspace);
      } else {
        renderStageNavigation(null);
        showPage("blocks");
      }
      renderTeachingSelector();
      renderTeachingList(serverState.precision_teachings);
      populateTeachingEditor(getTeachingById(serverState.current_teaching_id));
      showToast(`已删除“${teachingTitle}”`);
    } catch (error) {
      deleteTeachingButton.disabled = false;
      deleteTeachingButton.textContent = "删除";
      showToast(`删除失败：${error.message}`);
    }
    return;
  }

  const pageButton = event.target.closest("[data-page]");
  if (pageButton) {
    if (pageButton.dataset.openTeaching) {
      try {
        await chooseCurrentTeaching(pageButton.dataset.openTeaching);
      } catch (error) {
        showToast(`切换失败：${error.message}`);
        return;
      }
    }
    if (pageButton.dataset.page === "block-edit" && needsProfileSetup()) {
      showPage("profile");
      showToast("请先完善教师与班级信息");
      return;
    }
    if (!stageAccessible(pageButton.dataset.page)) {
      showToast(!serverState?.current_workspace ? "请先创建一项精准教学" : pageButton.dataset.page === "feedback" ? "请先完成并发布诊断任务" : "请先完成诊断结果反馈并由教师确认");
      return;
    }
    showPage(pageButton.dataset.page);
    if (pageButton.dataset.openOutput) {
      setInterventionStep(6);
      setOutputTab(pageButton.dataset.openOutput);
    }
    return;
  }

  const diagnosisStep = event.target.closest("[data-dstep], [data-dnext]");
  if (diagnosisStep) {
    const targetStep = Number(diagnosisStep.dataset.dstep || diagnosisStep.dataset.dnext);
    if (targetStep === 3) {
      await openRubricStep();
      return;
    }
    if (targetStep === 4 && serverState.current_workspace?.rubric?.status !== "confirmed") {
      showToast("请先检查并确认本任务的 SOLO 分析标准");
      return;
    }
    setDiagnosisStep(targetStep);
    return;
  }

  const interventionStep = event.target.closest("[data-istep], [data-istep-target]");
  if (interventionStep) {
    setInterventionStep(interventionStep.dataset.istep || interventionStep.dataset.istepTarget);
    return;
  }

  const feedbackTab = event.target.closest("[data-feedback-tab]");
  if (feedbackTab) {
    setFeedbackTab(feedbackTab.dataset.feedbackTab);
    return;
  }

  const studentButton = event.target.closest("[data-student]");
  if (studentButton) {
    document.querySelectorAll("[data-student]").forEach(button => button.classList.toggle("active", button === studentButton));
    const name = studentButton.dataset.student;
    document.getElementById("studentRecordName").textContent = `${name}的学习记录`;
    document.getElementById("studentFeedbackName").textContent = `${name}的个体反馈报告`;
    const feedbackStatus = document.getElementById("studentFeedbackStatus");
    const pushButton = document.getElementById("pushStudentFeedback");
    feedbackStatus.textContent = ui.allFeedbackPushed ? "已推送" : "未推送";
    feedbackStatus.className = ui.allFeedbackPushed ? "status success" : "status neutral";
    pushButton.textContent = ui.allFeedbackPushed ? "已推送" : "确认并推送反馈报告";
    pushButton.disabled = ui.allFeedbackPushed;
    return;
  }

  const outputTab = event.target.closest("[data-output-tab]");
  if (outputTab) {
    setOutputTab(outputTab.dataset.outputTab);
    return;
  }

  const activityToggle = event.target.closest(".activity-toggle");
  if (activityToggle) {
    activityToggle.closest(".activity-card").classList.toggle("expanded");
    return;
  }

  if (event.target.closest("#saveDiagnosisDraftButton")) {
    const button = event.target.closest("#saveDiagnosisDraftButton");
    button.disabled = true;
    button.textContent = "正在保存…";
    try {
      await saveCurrentDiagnosis("draft");
      showToast("诊断任务草稿已保存到当前精准教学");
    } catch (error) {
      showToast(`保存失败：${error.message}`);
    } finally {
      button.disabled = false;
      button.textContent = "保存草稿";
    }
    return;
  }

  if (event.target.closest("#publishTaskButton")) {
    const button = event.target.closest("#publishTaskButton");
    if (!serverState.classrooms.length) {
      showToast("请先在教师与班级背景信息中添加班级");
      return;
    }
    if (!document.querySelector('#publishClassOptions input[name="publishClassroom"]:checked')) {
      showToast("请至少选择一个发布班级");
      return;
    }
    button.disabled = true;
    button.textContent = "正在发布…";
    try {
      const workspace = await saveCurrentDiagnosis("published");
      showPage("feedback");
      showToast(workspace.feedback?.has_data ? "诊断任务已更新，已加载对应反馈数据" : "诊断任务已发布，等待学生提交后生成反馈");
    } catch (error) {
      showToast(`发布失败：${error.message}`);
    } finally {
      button.disabled = false;
      button.textContent = serverState.current_workspace?.diagnosis.status === "published" ? "更新已发布任务" : "确认发布";
    }
    return;
  }

  const proposalButton = event.target.closest("[data-select-proposal]");
  if (proposalButton) {
    const recommendation = ui.diagnosticRecommendations?.candidates.find(item => item.candidate_id === proposalButton.dataset.selectProposal);
    if (!recommendation) return;
    document.querySelectorAll(".proposal").forEach(card => {
      const selected = card.dataset.proposal === proposalButton.dataset.selectProposal;
      card.classList.toggle("selected", selected);
      const button = card.querySelector("[data-select-proposal]");
      button.textContent = selected ? "已采用" : "采用此方案";
      button.classList.toggle("primary", selected);
    });
    document.getElementById("diagnosisTaskText").value = recommendation.task_content;
    document.getElementById("diagnosisRoleText").value = [
      `AI 角色：${recommendation.ai_role}`,
      `应遵循：${recommendation.dialogue_rules.required.join("；")}`,
      `禁止行为：${recommendation.dialogue_rules.prohibited.join("；")}`
    ].join("\n");
    document.getElementById("diagnosisDuration").value = recommendation.estimated_duration_minutes;
    document.getElementById("publishDuration").textContent = `${recommendation.estimated_duration_minutes} 分钟`;
    showToast("已填入任务、AI 角色与规则和预计时长。请检查并保存草稿，再生成量规。");
    return;
  }

  const pathButton = event.target.closest("[data-select-path]");
  if (pathButton) {
    document.querySelectorAll("[data-path]").forEach(card => {
      const selected = card.dataset.path === pathButton.dataset.selectPath;
      card.classList.toggle("selected", selected);
      const button = card.querySelector("[data-select-path]");
      button.textContent = selected ? "已选择" : "选择此路径";
      button.classList.toggle("primary", selected);
    });
    return;
  }

  if (event.target.closest("#regenerateTasks")) {
    const button = event.target.closest("#regenerateTasks");
    button.disabled = true;
    button.textContent = "正在生成…";
    document.getElementById("taskRecommendationStatus").textContent = "正在根据当前精准教学信息生成三个方案，请稍候…";
    try {
      const teachingId = serverState.current_teaching_id;
      const response = await window.PlatformAPI.generateDiagnosticTaskRecommendations(teachingId);
      if (serverState.current_teaching_id === teachingId) {
        renderDiagnosticRecommendations(response.result, response.status);
        showToast("三个诊断任务方案已生成，请选择或继续编辑");
      }
    } catch (error) {
      document.getElementById("taskRecommendationStatus").textContent = `生成失败：${error.message}`;
      showToast(`诊断任务生成失败：${error.message}`);
    } finally {
      button.disabled = false;
      button.textContent = ui.diagnosticRecommendations ? "AI 重新推荐" : "AI 推荐 3 个方案";
    }
    return;
  }

  if (event.target.closest("#regenerateRubric")) {
    if (ui.rubricGenerating) return;
    ui.rubricGenerating = true;
    renderRubric(serverState.current_workspace?.rubric);
    try {
      await generateCurrentRubric();
      showToast("已重新生成分析标准，请检查后确认");
    } catch (error) {
      showToast(`量规生成失败：${error.message}`);
    } finally {
      ui.rubricGenerating = false;
      renderRubric(serverState.current_workspace?.rubric);
    }
    return;
  }

  if (event.target.closest("#analyzeCurrentEvidence")) {
    const button = event.target.closest("#analyzeCurrentEvidence");
    button.textContent = "已选择基于当前数据分析";
    button.disabled = true;
    document.getElementById("evidenceInsufficientMessage").textContent = "教师已选择不继续补充证据；AI将基于现有数据进行审慎分析，并在结果中保留证据限制说明。";
    showToast("已按当前数据分析，结果将保留证据不足说明");
    return;
  }

  if (event.target.closest("#completeFeedbackButton")) {
    const button = event.target.closest("#completeFeedbackButton");
    button.disabled = true;
    button.textContent = "正在确认…";
    try {
      const workspace = await window.PlatformAPI.confirmFeedback(serverState.current_teaching_id);
      storeWorkspace(workspace);
      showPage("intervention");
      showToast("诊断反馈已确认，精准干预设计现已开放");
    } catch (error) {
      showToast(`暂时不能进入干预设计：${error.message}`);
      applyWorkspace(serverState.current_workspace);
    }
    return;
  }

  const reportSection = event.target.closest("[data-edit-report-section]");
  if (reportSection) { startTeacherReportSectionEdit(reportSection); return; }

  const generateStudentReportButton = event.target.closest("[data-generate-student-report]");
  if (generateStudentReportButton) {
    const result = (serverState.studentResults?.results || []).find(item => item.id === generateStudentReportButton.dataset.generateStudentReport);
    if (!result) return;
    generateStudentReportButton.disabled = true;
    generateStudentReportButton.textContent = "正在生成…";
    try {
      await generateReportForResult(result);
      await loadStudentResults(serverState.current_teaching_id);
      showToast(`已生成${result.student.name}的个体报告`);
    } catch (error) {
      showToast(`报告生成失败：${error.message}`);
      generateStudentReportButton.disabled = false;
      generateStudentReportButton.textContent = result.report ? "重新生成" : "生成报告";
    }
    return;
  }

  if (event.target.closest("#generateAllStudentReports")) {
    const button = event.target.closest("#generateAllStudentReports");
    const pending = (serverState.studentResults?.results || []).filter(
      item => item.student.classroom_id === ui.activeClassroomId && (!item.report || item.report.status === "stale")
    );
    if (!pending.length) {
      showToast("当前没有待生成的学生报告");
      return;
    }
    button.disabled = true;
    let completed = 0;
    try {
      for (const result of pending) {
        button.textContent = `正在生成 ${completed + 1}/${pending.length}`;
        await generateReportForResult(result, { rerender: false });
        completed += 1;
      }
      renderStudentResults(serverState.studentResults);
      await loadStudentResults(serverState.current_teaching_id);
      showToast(`已生成 ${completed} 份学生报告`);
    } catch (error) {
      renderStudentResults(serverState.studentResults);
      showToast(`已完成 ${completed} 份，随后失败：${error.message}`);
    }
    return;
  }

  if (event.target.closest("#pushStudentFeedback")) {
    const pushButton = event.target.closest("#pushStudentFeedback");
    pushButton.disabled = true;
    const result = currentStudentResult();
    try {
      const updated = await window.PlatformAPI.saveStudentReport(
        result.id,
        result.report.report_text,
        pushButton.dataset.reportStatus
      );
      result.report = updated.report;
      renderStudentResults(serverState.studentResults);
      showToast(updated.report.status === "pushed" ? `报告已推送给${result.student.name}` : `已确认${result.student.name}的报告`);
    } catch (error) {
      pushButton.disabled = false;
      showToast(`报告操作失败：${error.message}`);
    }
    return;
  }

  if (event.target.closest("#pushConfirmedStudentReports")) {
    const button = event.target.closest("#pushConfirmedStudentReports");
    const pending = (serverState.studentResults?.results || []).filter(
      item => item.student.classroom_id === ui.activeClassroomId && item.report?.status === "confirmed" && !item.report._saving && !item.report._saveError && item.report.provider !== "mock"
    );
    if (!pending.length) {
      showToast("当前没有已确认、待推送的学生报告");
      return;
    }
    button.disabled = true;
    let completed = 0;
    try {
      for (const result of pending) {
        button.textContent = `正在推送 ${completed + 1}/${pending.length}`;
        const updated = await window.PlatformAPI.saveStudentReport(result.id, result.report.report_text, "pushed");
        result.report = updated.report;
        completed += 1;
      }
      showToast(`已推送 ${completed} 份已确认报告`);
    } catch (error) {
      showToast(`已推送 ${completed} 份，随后失败：${error.message}`);
    } finally {
      await loadStudentResults(serverState.current_teaching_id);
    }
    return;
  }

  if (event.target.closest("#pushAllFeedback")) {
    ui.allFeedbackPushed = true;
    const button = event.target.closest("#pushAllFeedback");
    button.textContent = "已全部推送";
    button.disabled = true;
    document.getElementById("studentPushSummary").textContent = "40名学生的反馈报告已全部推送。";
    document.getElementById("pendingFeedbackCount").textContent = "40 人已推送";
    document.getElementById("feedbackProgressStatus").textContent = "40 人已推送";
    document.querySelectorAll("[data-student] em").forEach(status => { status.textContent = "已推送"; });
    const feedbackStatus = document.getElementById("studentFeedbackStatus");
    feedbackStatus.textContent = "已推送";
    feedbackStatus.className = "status success";
    const singleButton = document.getElementById("pushStudentFeedback");
    singleButton.textContent = "已推送";
    singleButton.disabled = true;
    showToast("全部学生的反馈报告已推送");
    return;
  }

  const primaryPathButton = event.target.closest("[data-select-primary-path]");
  if (primaryPathButton) {
    selectPrimaryPath(primaryPathButton.dataset.selectPrimaryPath);
    markActivitySequencePending();
    showToast(`已将${interventionPathContent[ui.selectedPrimaryPath].name}设为主要路径`);
    return;
  }

  const pathDetailsButton = event.target.closest("[data-view-path-details]");
  if (pathDetailsButton) {
    openPathDetails(pathDetailsButton.dataset.viewPathDetails);
    return;
  }

  if (event.target.closest("#showPathPromptTemplate")) {
    const template = document.getElementById("pathPromptTemplate");
    const button = event.target.closest("#showPathPromptTemplate");
    template.hidden = !template.hidden;
    button.textContent = template.hidden ? "查看并复制组织模板" : "收起组织模板";
    return;
  }

  if (event.target.closest("#copyPathPromptTemplate")) {
    const copyButton = event.target.closest("#copyPathPromptTemplate");
    copyText(document.getElementById("pathPromptTemplateText").textContent).then(copied => {
      copyButton.textContent = copied ? "已复制" : "请手动复制";
      showToast(copied ? "课堂路径组织模板已复制" : "当前浏览器未允许复制，请手动选择模板文字");
      if (copied) window.setTimeout(() => { copyButton.textContent = "复制模板"; }, 1600);
    });
    return;
  }

  if (event.target.closest("#addProgressionGoal")) {
    addProgressionGoalRow();
    showToast("已增加一条可编辑的进阶目标");
    return;
  }

  const removeProgressionGoal = event.target.closest("[data-remove-progression-goal]");
  if (removeProgressionGoal) {
    const rows = document.querySelectorAll("#progressionGoalRows tr");
    if (rows.length <= 1) showToast("至少保留一条进阶目标");
    else {
      removeProgressionGoal.closest("tr").remove();
      showToast("进阶目标已删除");
    }
    return;
  }

  if (event.target.closest("#refreshPathRecommendation")) {
    const recommendation = document.querySelector(".path-recommendation p");
    recommendation.textContent = "根据当前共同障碍、学生差异和45分钟课堂条件，建议以递进推进型保持全班思维主线，在关系建构阶段并行分层，并为少数学生嵌入短时支持。教师可更换主要路径或修改下方要求。";
    showToast("已根据当前设计依据更新路径建议");
    return;
  }

  if (event.target.closest("#generateActivitySequence")) {
    const button = event.target.closest("#generateActivitySequence");
    rebuildInterventionSequence();
    button.textContent = "重新生成活动序列";
    document.getElementById("sequenceGenerationStatus").textContent = `已根据${interventionPathContent[ui.selectedPrimaryPath].name}和融合策略生成，可继续调整`;
    setInterventionStep(3);
    document.querySelector(".activity-design-card").scrollIntoView({ behavior: "smooth", block: "start" });
    showToast("活动序列已生成，顺序、时长和活动内容均可修改");
    return;
  }

  const editActivityButton = event.target.closest("[data-edit-activity]");
  if (editActivityButton) {
    renderInterventionActivityEditor(editActivityButton.dataset.editActivity);
    document.getElementById("activityDetailEditor").scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }

  const toggleActivityButton = event.target.closest("[data-toggle-sequence-activity]");
  if (toggleActivityButton) {
    const card = toggleActivityButton.closest("[data-sequence-activity]");
    card.classList.toggle("expanded");
    toggleActivityButton.textContent = card.classList.contains("expanded") ? "收起" : "展开";
    return;
  }

  const regenerateInlineActivity = event.target.closest("[data-regenerate-inline-activity]");
  if (regenerateInlineActivity) {
    const id = regenerateInlineActivity.dataset.regenerateInlineActivity;
    const activity = interventionActivities[id];
    activity.implementation = `${activity.implementation}\nAI建议：保留个人思考时间，再组织交流，并在活动结束时收集一份个人学习证据。`;
    renderInlineActivityDetails(regenerateInlineActivity.closest("[data-sequence-activity]"), id);
    showToast("已重新设计本活动，可继续直接修改");
    return;
  }

  const moveActivityButton = event.target.closest("[data-move-activity]");
  if (moveActivityButton) {
    moveInterventionActivity(moveActivityButton.closest("[data-sequence-activity]"), moveActivityButton.dataset.moveActivity);
    return;
  }

  if (event.target.closest("#addInterventionActivity")) {
    addInterventionActivity();
    showToast("已添加一个可编辑的学习活动");
    return;
  }

  const addInlineBranch = event.target.closest("[data-add-inline-branch]");
  if (addInlineBranch) {
    const id = addInlineBranch.dataset.addInlineBranch;
    const activity = interventionActivities[id];
    activity.branches.push({ name: "新小组／学习站", target: "选择进阶目标", students: "填写学生姓名或分组条件", task: "填写学习任务", support: "填写支架与资源" });
    renderInlineActivityDetails(addInlineBranch.closest("[data-sequence-activity]"), id);
    showToast("已在本活动中增加一个并行分支");
    return;
  }

  const removeInlineBranch = event.target.closest("[data-remove-inline-branch]");
  if (removeInlineBranch) {
    const card = removeInlineBranch.closest("[data-sequence-activity]");
    const activity = interventionActivities[card.dataset.sequenceActivity];
    if (activity.branches.length <= 1) showToast("至少保留一个小组或学习站");
    else {
      activity.branches.splice(Number(removeInlineBranch.dataset.removeInlineBranch), 1);
      renderInlineActivityDetails(card, card.dataset.sequenceActivity);
      showToast("本活动中的并行分支已删除");
    }
    return;
  }

  if (event.target.closest("#addParallelBranch")) {
    addParallelBranchRow();
    showToast("已增加一个可编辑的并行分支");
    return;
  }

  const removeParallelBranch = event.target.closest("[data-remove-parallel-branch]");
  if (removeParallelBranch) {
    const rows = document.querySelectorAll("#parallelBranchRows tr");
    if (rows.length <= 1) showToast("至少保留一个小组或学习站");
    else {
      removeParallelBranch.closest("tr").remove();
      showToast("并行分支已删除");
    }
    return;
  }

  if (event.target.closest("#collapseActivityEditor")) {
    saveActiveInterventionActivity();
    document.getElementById("activityDetailEditor").hidden = true;
    showToast("本活动修改已自动保存");
    return;
  }

  if (event.target.closest("#regenerateSelectedActivity")) {
    saveActiveInterventionActivity();
    const activity = interventionActivities[ui.activeInterventionActivity];
    activity.implementation = `${activity.implementation}\nAI建议：先保留个人思考时间，再进行交流，并在活动结束时收集一份可独立判断的学生证据。`;
    renderInterventionActivityEditor(ui.activeInterventionActivity);
    showToast("已结合当前目标和路径重新设计本活动，可继续编辑");
    return;
  }

  if (event.target.closest("#regenerateInterventionGoals")) {
    showToast("已根据诊断信息更新进阶目标表，可继续编辑");
    return;
  }

  if (event.target.closest("#previewInterventionPlan")) {
    showInterventionPlanPreview();
    return;
  }

  if (event.target.closest("#refreshInterventionReport, #refreshInterventionReportEmpty")) {
    const button = event.target.closest("#refreshInterventionReport, #refreshInterventionReportEmpty");
    const teachingId = serverState.current_teaching_id;
    const classroomId = ui.activeInterventionClassroomId;
    button.disabled = true;
    button.textContent = "正在生成并审核…";
    const status = document.getElementById("integrationReportStatus");
    status.hidden = false;
    status.textContent = "正在生成并审核教学报告，请稍候…";
    try {
      const payload = await window.PlatformAPI.generateIntegrationReport(teachingId, classroomId);
      if (serverState.current_teaching_id !== teachingId || ui.activeInterventionClassroomId !== classroomId) return;
      showIntegrationReport({ ready: true, report: payload.report });
      showToast(`教学报告已生成：${payload.report.result.audit_summary.overall_conclusion}`);
    } catch (error) {
      document.getElementById("integrationReportStatus").textContent = `报告生成失败：${error.message}`;
      showToast(`报告生成失败：${error.message}`);
      if (button.isConnected) {
        button.disabled = false;
        button.textContent = button.id === "refreshInterventionReportEmpty" ? "AI生成教学报告" : "AI重新生成教学报告";
      }
    }
    return;
  }

  if (event.target.closest("#downloadInterventionPdf, #downloadInterventionReport")) {
    if (serverState?.integrationReport?.status !== "current") {
      showToast("请先生成当前版本的教学报告");
      return;
    }
    if (event.target.closest("#downloadInterventionPdf")) await downloadInterventionPdf(event.target.closest("#downloadInterventionPdf"));
    else downloadInterventionReport();
    return;
  }

  if (event.target.closest("#finishInterventionDesign")) {
    const button = event.target.closest("#finishInterventionDesign");
    button.disabled = true;
    button.textContent = "正在保存…";
    try {
      const workspace = await window.PlatformAPI.saveIntervention(serverState.current_teaching_id, {
        status: "completed",
        duration_minutes: Number(document.getElementById("interventionDurationMinutes").value || 45),
        evidence_summary: serverState.current_workspace?.intervention?.evidence_summary || "",
        teacher_judgment: serverState.current_workspace?.intervention?.teacher_judgment || ""
      });
      storeWorkspace(workspace);
      showToast("本节课干预方案已保存并标记为完成");
    } catch (error) {
      showToast(`保存失败：${error.message}`);
    } finally {
      button.disabled = false;
      button.textContent = "完成本节课设计";
    }
    return;
  }

  if (event.target.closest("#createBlockButton")) {
    const button = event.target.closest("#createBlockButton");
    button.disabled = true;
    button.textContent = "正在保存…";
    try {
      const teaching = await window.PlatformAPI.createTeaching({
        title: document.getElementById("blockTheme").value.trim(),
        goal: document.getElementById("blockGoal").value.trim(),
        content: document.getElementById("blockContent").value.trim(),
        rationale: document.getElementById("blockRationale").value.trim(),
        subject: document.getElementById("blockSubject").value,
        grade: document.getElementById("blockGrade").value,
        textbook: document.getElementById("blockTextbook").value.trim(),
        estimated_periods: Number(document.getElementById("blockPeriods").value || 1)
      });
      serverState.precision_teachings.unshift(teaching);
      serverState.current_teaching_id = teaching.id;
      serverState.current_workspace = await window.PlatformAPI.getWorkspace(teaching.id);
      storeWorkspace(serverState.current_workspace);
      showPage("diagnosis");
      showToast("精准教学已保存，进入诊断设计");
    } catch (error) {
      showToast(`保存失败：${error.message}`);
    } finally {
      button.disabled = false;
      button.textContent = "保存并进入诊断设计";
    }
    return;
  }

  if (event.target.closest("#saveProfileButton")) {
    const button = event.target.closest("#saveProfileButton");
    button.disabled = true;
    button.textContent = "正在保存…";
    try {
      const profile = await window.PlatformAPI.saveProfile({
        display_name: document.getElementById("profileDisplayName").value.trim(),
        subject: document.getElementById("profileSubject").value.trim(),
        years_experience: Number(document.getElementById("profileYears").value || 0),
        teaching_style: document.getElementById("profileTeachingStyle").value.trim()
      });
      updateTeacherIdentity(profile);
      serverState.teacher = profile;
      populateTeachingSubjects(profile.subject);
      renderProfileSetup();
      renderTeachingList(serverState.precision_teachings);
      if (serverState.classrooms.length) showPage("blocks");
      showToast(serverState.classrooms.length ? "教师与班级信息已完成，可以开启精准教学" : "教师信息已保存，请继续添加班级");
    } catch (error) {
      showToast(`保存失败：${error.message}`);
    } finally {
      button.disabled = false;
      button.textContent = "保存设置";
    }
    return;
  }

  const styleSuggestion = event.target.closest("[data-style-suggestion]");
  if (styleSuggestion) {
    const field = document.getElementById("profileTeachingStyle");
    const suggestion = styleSuggestion.textContent.trim();
    if (!field.value.includes(suggestion)) {
      const current = field.value.trimEnd();
      field.value = current ? `${current}${/[；;，,。\s]$/.test(current) ? "" : "；"}${suggestion}` : suggestion;
      field.dispatchEvent(new Event("input", { bubbles: true }));
    }
    field.focus();
    field.setSelectionRange(field.value.length, field.value.length);
    return;
  }

  if (event.target.closest("#mobileMenu")) {
    document.getElementById("sidebar").classList.toggle("open");
    return;
  }

  const unimplemented = event.target.closest("button");
  if (unimplemented && !unimplemented.matches(".help-dot")) {
    showToast("该操作尚未接入当前本地版本");
  }
});

function closeActivityEditBlock(block) {
  block?.querySelector(".af-block-editor")?.remove();
  block?.classList.remove("is-editing");
}

document.addEventListener("click", event => {
  const block = event.target.closest(".af-edit-block");
  document.querySelectorAll(".af-edit-block.is-editing").forEach(open => {
    if (open !== block) closeActivityEditBlock(open);
  });
  if (!block || block.classList.contains("is-editing")) return;
  const root = document.getElementById("activityFormativeGeneratedContent");
  const targets = [...block.querySelectorAll(".af-inline-text")];
  const editor = document.createElement("div");
  editor.className = "af-block-editor";
  if (block.dataset.afUnified) {
    const activityIndex = Number(block.dataset.afActivityIndex);
    const kind = block.dataset.afUnified;
    const overrideField = root.querySelector(`[data-af-presentation='${activityIndex}:${kind}']`);
    const wrapper = document.createElement("label");
    wrapper.textContent = block.dataset.afEditLabel;
    const textarea = document.createElement("textarea");
    textarea.rows = Math.max(5, Math.min(12, targets.length * 2 + 2));
    const initialTargets = kind === "student"
      ? targets.filter(display => display.dataset.afInlineTarget.startsWith("[data-af-action"))
      : targets;
    textarea.value = overrideField.value || initialTargets.map(display => {
      const group = display.closest(".af-group-line")?.querySelector("b")?.textContent;
      return `${group ? `${group}：` : ""}${root.querySelector(display.dataset.afInlineTarget)?.value || display.textContent}`;
    }).join(kind === "student" ? "\n" : "\n\n");
    if (kind === "student" && !textarea.value) {
      textarea.value = serverState?.activityFormative?.design?.result?.activities?.[activityIndex]?.student_task || "";
    }
    let preview;
    if (kind === "student") {
      const hint = document.createElement("small");
      hint.textContent = "每行一项，编号自动生成";
      wrapper.append(hint);
      preview = document.createElement("div");
      preview.className = "af-editor-list-preview";
      preview.innerHTML = renderNumberedActivityLines(textarea.value);
    }
    textarea.addEventListener("input", () => {
      const value = textarea.value;
      overrideField.value = value;
      const base = targets[0];
      if (kind === "student") {
        const list = block.querySelector("ol.af-action-list");
        if (list) list.outerHTML = renderNumberedActivityLines(value);
        preview.innerHTML = renderNumberedActivityLines(value);
      } else if (base) {
        base.textContent = value;
      }
      block.querySelector(".af-group-summary")?.setAttribute("hidden", "");
    });
    wrapper.append(textarea);
    editor.append(wrapper);
    if (preview) editor.append(preview);
    block.classList.add("is-editing");
    block.append(editor);
    textarea.focus();
    return;
  }
  const makeField = (source, display, label) => {
    const wrapper = document.createElement("label");
    wrapper.textContent = label;
    const textarea = source?.tagName === "SELECT" ? document.createElement("select") : document.createElement("textarea");
    if (source?.tagName === "SELECT") textarea.innerHTML = source.innerHTML;
    textarea.rows = Math.max(3, Math.min(8, (source?.value || "").split("\n").length + 1));
    textarea.value = source?.value || "";
    textarea.addEventListener(source?.tagName === "SELECT" ? "change" : "input", () => {
      if (source) source.value = textarea.value;
      if (display) display.textContent = display.dataset.afInlineMaterials
        ? textarea.value.split("\n").map(item => item.trim()).filter(Boolean).join("、")
        : textarea.value;
      if (source?.tagName === "SELECT") source.dispatchEvent(new Event("input", {bubbles: true}));
    });
    wrapper.append(textarea);
    editor.append(wrapper);
    return textarea;
  };
  const actionTargets = targets.filter(display => display.dataset.afInlineTarget.startsWith("[data-af-action"));
  if (actionTargets.length) {
    const wrapper = document.createElement("label");
    wrapper.textContent = block.dataset.afEditLabel;
    const textarea = document.createElement("textarea");
    textarea.rows = Math.max(3, actionTargets.length + 1);
    textarea.value = actionTargets.map(display => root.querySelector(display.dataset.afInlineTarget)?.value || "").join("\n");
    textarea.addEventListener("input", () => {
      const lines = textarea.value.split("\n");
      actionTargets.forEach((display, index) => {
        const value = index === actionTargets.length - 1 ? lines.slice(index).join("\n") : (lines[index] || "");
        root.querySelector(display.dataset.afInlineTarget).value = value;
        display.textContent = value;
      });
    });
    wrapper.append(textarea);
    editor.append(wrapper);
  }
  targets.filter(display => !actionTargets.includes(display)).forEach((display, index) => {
    const source = root.querySelector(display.dataset.afInlineTarget);
    const group = display.closest(".af-group-line")?.querySelector("b")?.textContent;
    makeField(source, display, group || (targets.length > 1 ? `${block.dataset.afEditLabel} ${index + 1}` : block.dataset.afEditLabel));
  });
  if (!targets.length && block.dataset.afEditLabel === "AI辅助") {
    const card = block.closest(".activity-lesson-card");
    const index = [...card.parentElement.querySelectorAll(".activity-lesson-card")].indexOf(card);
    makeField(root.querySelector(`[data-af-new-action='${index}:AI']`), null, "AI辅助");
  }
  if (!editor.children.length) return;
  block.classList.add("is-editing");
  block.append(editor);
  editor.querySelector("textarea, select")?.focus();
});

document.addEventListener("focusout", event => {
  const block = event.target.closest(".af-edit-block.is-editing");
  if (block && !block.contains(event.relatedTarget)) closeActivityEditBlock(block);
}, true);

document.addEventListener("input", event => {
  if (event.target.id === "profileTeachingStyle") syncStyleSuggestions();
  if (event.target.closest("#activityFormativeGeneratedContent")) {
    ui.activityFormativeDirty = true;
    document.getElementById("nextFromActivityFormative").disabled = true;
    document.getElementById("activityFormativeStatus").textContent = "有未保存的修改";
    document.querySelector('#interventionSteps [data-istep="4"]').disabled = true;
  }
  if (event.target.closest("#goalPathGeneratedContent")) {
    document.getElementById("nextFromGoalPath").disabled = true;
    document.getElementById("goalPathStatus").textContent = "有未保存的修改";
  }
  if (event.target.matches(".minute-input")) updateTimeTotal();
  if (event.target.matches(".intervention-minute-input")) updateInterventionTimeTotal();
  if (event.target.id === "interventionDurationMinutes") {
    updateInterventionDurationContext();
    markActivitySequencePending();
  }
  if (event.target.matches("[data-inline-activity-field], [data-inline-branch-field]")) saveInlineActivityField(event.target);
  if (event.target.id === "diagnosisDuration") {
    document.getElementById("publishDuration").textContent = `${event.target.value || 0} 分钟`;
  }
  if (event.target.matches("#rubricTableBody [contenteditable='true']")) {
    const record = serverState.current_workspace?.rubric;
    if (!record?.rubric) return;
    record.status = "draft";
    document.getElementById("rubricConfirmed").checked = false;
    document.getElementById("rubricNextButton").disabled = true;
    const state = document.getElementById("rubricRuntimeStatus");
    state.textContent = `内容已修改，等待重新确认 · ${record.skill_key} v${record.skill_version}`;
    state.className = "rubric-runtime-status draft";
    window.clearTimeout(rubricSaveTimer);
    rubricSaveTimer = window.setTimeout(async () => {
      try {
        await persistRubric(false, { rerender: false });
      } catch (error) {
        showToast(`量规草稿保存失败：${error.message}`);
      }
    }, 700);
  }
});

document.addEventListener("change", async event => {
  if (event.target.id === "interventionClassSelect") {
    ui.activeInterventionClassroomId = event.target.value;
    ui.goalPathAppliedKey = null;
    serverState.activityFormative = null;
    ui.activityFormativeError = null;
    loadGoalPath(serverState.current_teaching_id, ui.activeInterventionClassroomId);
    if (ui.interventionStep === 3) loadActivityFormative(serverState.current_teaching_id, ui.activeInterventionClassroomId);
    return;
  }
  if (event.target.id === "feedbackClassSelect") {
    ui.activeClassroomId = event.target.value;
    ui.activeStudentResultIndex = 0;
    if (serverState.studentResults) renderStudentResults(serverState.studentResults);
    return;
  }
  if (event.target.id === "rubricConfirmed") {
    event.target.disabled = true;
    window.clearTimeout(rubricSaveTimer);
    try {
      await persistRubric(event.target.checked);
      showToast(event.target.checked ? "SOLO 分析标准已确认，可以进入发布预览" : "量规已改为待确认状态");
    } catch (error) {
      event.target.checked = !event.target.checked;
      showToast(`量规保存失败：${error.message}`);
    } finally {
      event.target.disabled = false;
    }
    return;
  }
  if (event.target.id === "currentTeachingSelect") {
    const teachingId = event.target.value;
    event.target.disabled = true;
    try {
      const teaching = await chooseCurrentTeaching(teachingId);
      showToast(`已切换到“${teaching.title}”`);
    } catch (error) {
      renderTeachingSelector();
      showToast(`切换失败：${error.message}`);
    } finally {
      event.target.disabled = false;
    }
    return;
  }
  if (event.target.matches("[data-fusion-strategy]")) {
    const selected = getSelectedFusionStrategies();
    if (selected.length > 2) {
      event.target.checked = false;
      showToast("融合策略最多选择2项");
    }
    updateFusionSummary();
    markActivitySequencePending();
  }
  if (event.target.matches(".goal-level-select")) {
    const customInput = event.target.closest("td").querySelector(".custom-goal-level-input");
    customInput.hidden = event.target.value !== "自定义进阶目标";
    if (!customInput.hidden) customInput.focus();
  }
  if (event.target.matches("[data-inline-goal-template]")) {
    const context = getInlineActivityContext(event.target);
    const option = event.target.selectedOptions[0];
    if (context?.activity && option?.value) {
      const field = context.card.querySelector('[data-inline-activity-field="goal"]');
      context.activity.goalSource = option.value;
      context.activity.goal = option.dataset.goalText || "";
      field.value = context.activity.goal;
      showToast(`已引用“${option.value}”，仍可继续修改`);
    }
  }
  if (event.target.matches("[data-inline-activity-field]")) {
    saveInlineActivityField(event.target);
    if (event.target.dataset.inlineActivityField === "structure" || event.target.dataset.inlineActivityField === "organization") refreshInlineActivityStructure(event.target);
  }
  if (event.target.matches("[data-inline-checkpoint]")) {
    const context = getInlineActivityContext(event.target);
    context.activity.checkpoint = event.target.checked;
    context.card.querySelector(".inline-checkpoint-fields").hidden = !event.target.checked;
  }
});

document.addEventListener("focusin", event => {
  if (event.target.matches("[data-inline-goal-template]")) {
    const current = event.target.value;
    populateGoalTemplateSelect(event.target, current);
  }
});

const interventionActivityList = document.getElementById("sortableActivityList");
interventionActivityList.addEventListener("dragstart", event => {
  const card = event.target.closest("[data-sequence-activity]");
  if (!card) return;
  ui.draggedInterventionActivity = card;
  card.classList.add("dragging");
  event.dataTransfer.effectAllowed = "move";
});

interventionActivityList.addEventListener("dragover", event => {
  event.preventDefault();
  const target = event.target.closest("[data-sequence-activity]");
  const dragged = ui.draggedInterventionActivity;
  if (!target || !dragged || target === dragged) return;
  const rect = target.getBoundingClientRect();
  const after = event.clientY > rect.top + rect.height / 2;
  target.parentElement.insertBefore(dragged, after ? target.nextElementSibling : target);
});

interventionActivityList.addEventListener("dragend", () => {
  if (ui.draggedInterventionActivity) ui.draggedInterventionActivity.classList.remove("dragging");
  ui.draggedInterventionActivity = null;
  renumberInterventionActivities();
});

document.getElementById("taskGuideModal").addEventListener("click", event => {
  if (event.target.id === "taskGuideModal") closeTaskGuide();
});

document.getElementById("interventionHelpModal").addEventListener("click", event => {
  if (event.target.id === "interventionHelpModal") closeInterventionHelp();
});

document.addEventListener("keydown", event => {
  if ((event.key === "Enter" || event.key === " ") && event.target.matches("[data-edit-report-section]")) {
    event.preventDefault();
    startTeacherReportSectionEdit(event.target);
  }
  if (event.key === "Escape" && !document.getElementById("taskGuideModal").hidden) closeTaskGuide();
  if (event.key === "Escape" && !document.getElementById("interventionHelpModal").hidden) closeInterventionHelp();
});

updateTimeTotal();
updateFusionSummary();
renderAllInlineActivityDetails();
updateInterventionDurationContext();
updateInterventionTimeTotal();
renumberInterventionActivities();
initializeTeacherAuth();

window.setInterval(() => {
  if (ui.currentPage !== "feedback" || !document.querySelector('[data-feedback-tab="class"]').classList.contains("active")) return;
  const report = serverState?.studentResults?.class_reports?.[ui.activeClassroomId];
  if (report && ["queued", "running"].includes(report.status) && serverState.current_teaching_id) {
    loadStudentResults(serverState.current_teaching_id);
  }
}, 7000);
