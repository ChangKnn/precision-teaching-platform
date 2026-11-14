'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const root = path.resolve(__dirname, '../..');
const code = fs.readFileSync(path.join(root, 'frontend/app.js'), 'utf8');
const start = code.indexOf('function renderClassReport(payload)');
const renderer = code.slice(start, code.indexOf('\nfunction renderStudentResults', start));
const data = JSON.parse(fs.readFileSync(path.join(root, 'backend/skills/solo-class-diagnosis-intervention/tests/sample-output.json'), 'utf8'));
const students = data.class_summary.distribution.flatMap(row => row.students);
students.forEach((student, i) => { student.student_name = `学生${i + 1}`; });
const [first, second] = students;
data.grouping_recommendations.homogeneous_groups = [{group_id:'H1',group_name:'第1学习小组',students,
  common_characteristics:'本组由不同学习需要的学生组成，请按具体需要提供支持。',
  progression_direction:'支持一；支持二',use_case:'课堂',
  support_breakdown:[{student_ids:[first.student_id],support:'只给第一位学生的支持'}]}];
data.grouping_recommendations.heterogeneous_groups = [];
data.individual_attention = [{student_id:second.student_id,category:'情绪表达',observation:'不应展示未经核对的情绪判断',evidence_quote:'快一点',teacher_action:'询问需要'}];
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id, {innerHTML:'',textContent:'',querySelectorAll:()=>[]}); return elements.get(id); };
const context = {document:{getElementById:get},ui:{activeClassroomId:'c'},serverState:{current_workspace:{}},
  escapeHTML:value=>String(value??''),singletonGroupingNote:()=>'',renderCustomClassReport:()=>'',initializeClassFeedbackNavigation:()=>{}};
vm.runInNewContext(renderer+';this.render=renderClassReport;', context);
const results = students.map(student=>({student:{id:student.student_id,classroom_id:'c'},report:{result:{}},messages:[]}));
context.render({classrooms:[{id:'c',name:'测试班'}],results,class_reports:{c:{status:'ready',result:data}}});
const html = get('classEvidencePanel').innerHTML;
assert(html.includes('<b>学情：</b>'));
assert(!html.includes('<b>学习需要：</b>'));
assert(!html.includes('查看组内支持安排'));
assert(!html.includes('只给第一位学生的支持'));
assert(html.includes('学生表达（待教师核对）'));
assert(!html.includes('不应展示未经核对的情绪判断'));
assert(html.includes('快一点'));
console.log('组内支持对应及学生表达呈现检查通过');
