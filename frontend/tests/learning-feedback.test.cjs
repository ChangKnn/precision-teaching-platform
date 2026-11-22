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
test('task feedback toolbar is hidden without content and shows only available feedback', () => {
  const toolbar = {innerHTML:'', hidden:false};
  const task = {teaching_id:'a',learning_feedback:{thinking_ready:true,examples_ready:true}};
  const state = {tasks:[task],activeTask:task,session:{status:'in_progress'}};
  const c = load(['renderLearningFeedbackButtons'], {state,$:()=>toolbar,$$:()=>[]});
  c.renderLearningFeedbackButtons();
  assert.equal(toolbar.hidden,true);assert.equal(toolbar.innerHTML,'');
  state.session.status='submitted';task.learning_feedback={thinking_ready:true,examples_ready:false};
  c.renderLearningFeedbackButtons();
  assert.equal(toolbar.hidden,false);assert.match(toolbar.innerHTML,/我的思维反馈/);assert.doesNotMatch(toolbar.innerHTML,/优秀对话示例/);
  task.learning_feedback={thinking_ready:false,examples_ready:true};c.renderLearningFeedbackButtons();
  assert.match(toolbar.innerHTML,/优秀对话示例/);assert.doesNotMatch(toolbar.innerHTML,/我的思维反馈/);
  task.learning_feedback={};c.renderLearningFeedbackButtons();assert.equal(toolbar.hidden,true);assert.equal(toolbar.innerHTML,'');
  state.activeTask={teaching_id:'other'};c.renderLearningFeedbackButtons();assert.equal(toolbar.hidden,true);
});
test('submitted polling checks lightweight status without reloading conversation or overwriting drafts', async () => {
  let poll; const paths=[];
  const state={session:{id:'s',teaching_id:'t',status:'submitted'},tasks:[{teaching_id:'t'}]};
  const c=load(['refreshLearningFeedbackStatus','stopReportPolling','startReportPolling'],{
    state,document:{hidden:false},window:{clearInterval(){},setInterval(fn){poll=fn;return 1;}},
    api:async path=>{paths.push(path);return {thinking_ready:true,examples_ready:true};},renderHistory(){},renderLearningFeedbackButtons(){},
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
