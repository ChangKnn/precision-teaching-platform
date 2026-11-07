const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('frontend/app.js','utf8');
function render(report){
 const panel={innerHTML:''};
 const context=vm.createContext({document:{getElementById:()=>panel},serverState:{current_workspace:{rubric:{status:'confirmed'}}},
  escapeHTML:x=>String(x||''),formatUpdatedAt:()=>'',renderCustomIndividualReport:()=>'',renderIndividualFeedbackOverview:()=>'',window:{TaskMath:{typeset(){}}}});
 vm.runInContext(source.match(/function renderStudentResultDetail\([^]*?\n\}/)[0],context);
 context.renderStudentResultDetail({id:'session',messages:[],student:{name:'测试学生',class_name:'测试班'},report});
 return panel.innerHTML;
}
test('completed detailed feedback is immediately visible rather than hidden behind a closed preview',()=>{
 const html=render({report_kind:'detailed',status:'draft',student_feedback_text:'已生成的详细反馈',result:{}});
 assert.match(html,/<details class="student-facing-card" open>/);
 assert.match(html,/已生成的详细反馈/);
});
test('brief analysis retains its on-demand detailed feedback entry',()=>{
 const html=render({report_kind:'brief',result:{diagnosis:{level_name:'关联结构',rationale:'依据'},progression:{main_obstacle:'具体障碍'}}});
 assert.match(html,/生成详细个体分析与反馈/);
 assert.doesNotMatch(html,/id="studentFeedbackEditor"/);
});
