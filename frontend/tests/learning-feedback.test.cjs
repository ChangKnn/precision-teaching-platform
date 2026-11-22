const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('frontend/student/student.js', 'utf8');
function load(names, values) {
  const c = vm.createContext(values);
  for (const name of names) vm.runInContext(source.match(new RegExp(`(?:async )?function ${name}\\([^]*?\\n\\}`))[0], c);
  return c;
}
test('sidebar feedback controls are disabled before submission and become ready independently', () => {
  const list = {innerHTML:''}, count = {};
  const tasks = [
    {teaching_id:'a',title:'未提交',session_status:'in_progress',learning_feedback:{thinking_ready:true,examples_ready:true}},
    {teaching_id:'b',title:'反馈已到',session_status:'submitted',learning_feedback:{thinking_ready:true,examples_ready:false}},
    {teaching_id:'c',title:'示例已到',session_status:'submitted',learning_feedback:{thinking_ready:false,examples_ready:true}}
  ];
  const c = load(['renderHistory'], {escapeHtml:value=>String(value || ''),state:{tasks}, $:selector=>selector==='#historyList'?list:count,$$:()=>[],studentTaskTitle:task=>task.title,formatTime:()=>''});
  c.renderHistory();
  const blocks = list.innerHTML.split('<div class="history-task">').slice(1);
  assert.equal(blocks.length,3);
  assert.equal((blocks[0].match(/ disabled/g)||[]).length,2);
  assert.match(blocks[1], /data-learning-kind="thinking" class="task-feedback-button ready"/);
  assert.match(blocks[1], /data-learning-kind="examples" class="task-feedback-button" disabled/);
  assert.match(blocks[2], /data-learning-kind="examples" class="task-feedback-button ready"/);
});
test('submitted polling checks lightweight status without reloading conversation or overwriting drafts', async () => {
  let poll; const paths=[];
  const state={session:{id:'s',teaching_id:'t',status:'submitted'},tasks:[{teaching_id:'t'}]};
  const c=load(['refreshLearningFeedbackStatus','stopReportPolling','startReportPolling'],{
    state,document:{hidden:false},window:{clearInterval(){},setInterval(fn){poll=fn;return 1;}},
    api:async path=>{paths.push(path);return {thinking_ready:true,examples_ready:true};},renderHistory(){},
    renderMessages(){throw Error('should not redraw conversation');}
  });
  c.startReportPolling();await poll();
  assert.deepEqual(paths,['/api/student/sessions/s/learning-feedback/status']);
  assert.equal(state.tasks[0].learning_feedback.examples_ready,true);
  c.document.hidden=true;await poll();assert.equal(paths.length,1);
});
test('a late status response cannot change another selected task', async () => {
  let complete;
  const state={session:{id:'old',teaching_id:'t',status:'submitted'},tasks:[{teaching_id:'t'}]};
  const c=load(['refreshLearningFeedbackStatus'],{state,api:()=>new Promise(resolve=>complete=resolve),renderHistory(){throw Error('stale update');}});
  const pending=c.refreshLearningFeedbackStatus();state.session={id:'new'};complete({examples_ready:true});await pending;
  assert.equal(state.tasks[0].learning_feedback,undefined);
});
