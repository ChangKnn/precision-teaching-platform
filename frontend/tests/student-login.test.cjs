const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('frontend/student/student.js','utf8');
function fixture(api) {
  const elements = new Map();
  const element = selector => { if (!elements.has(selector)) elements.set(selector, {textContent:'',innerHTML:'',addEventListener(){}});return elements.get(selector); };
  const context = vm.createContext({taskOpenRequest:0, state:{token:'logged-in',tasks:[{teaching_id:'task-a'},{teaching_id:'task-b'}]},
    window:{clearInterval(){}},stopReportPolling(){},renderTask(){},renderHistory(){},renderMessages(){},updateSubmissionSummary(){},startTimer(){},startReportPolling(){},resizeComposer(){},
    messages:{innerHTML:''},$:element,api,encodeURIComponent});
  vm.runInContext(source.match(/async function openTask\([^]*?\n\}/)[0],context);
  return context;
}
test('pending opening shows the task and prevents sending before a session exists',async()=>{
  let resolve;const pending=new Promise(done=>resolve=done);const c=fixture(()=>pending);
  const loading=c.openTask('task-a');
  assert.equal(c.state.activeTask.teaching_id,'task-a');assert.equal(c.state.session,null);assert.equal(c.state.taskLoading,true);
  assert.match(c.messages.innerHTML,/role="status"/);
  resolve({task:{teaching_id:'task-a'},session:{id:'session-a'}});await loading;
  assert.equal(c.state.session.id,'session-a');assert.equal(c.state.taskLoading,false);
});
test('a late opening cannot replace a newer selected task',async()=>{
  const completions=[];const c=fixture(()=>new Promise(resolve=>completions.push(resolve)));
  const first=c.openTask('task-a');const second=c.openTask('task-b');
  completions[1]({task:{teaching_id:'task-b'},session:{id:'session-b'}});await second;
  completions[0]({task:{teaching_id:'task-a'},session:{id:'session-a'}});await first;
  assert.equal(c.state.session.id,'session-b');
});
test('opening error offers retry and leaves no sendable session',async()=>{
  const c=fixture(async()=>{throw Error('offline');});await assert.rejects(c.openTask('task-a'),/offline/);
  assert.equal(c.state.session,null);assert.match(c.messages.innerHTML,/retryTaskOpening/);
});
// Viewport behavior is tested without a microphone, network, or real student changes.
test('student layout follows the visible keyboard viewport without shrinking pinch zoom',()=>{
  let height;const listeners={};const viewport={height:420,scale:1,addEventListener:(name,fn)=>listeners[name]=fn};
  const match=source.match(/function syncStudentViewport\([^]*?\n\}/);
  const c=vm.createContext({window:{innerHeight:844,visualViewport:viewport},document:{documentElement:{style:{setProperty:(_,value)=>height=value}}}});
  vm.runInContext(match[0],c);c.syncStudentViewport();assert.equal(height,'420px');
  viewport.height=210;viewport.scale=2;c.syncStudentViewport();assert.equal(height,'420px');
  viewport.height=844;viewport.scale=1;c.syncStudentViewport();assert.equal(height,'844px');
});
