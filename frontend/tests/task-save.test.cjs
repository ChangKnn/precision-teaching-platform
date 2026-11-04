const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('frontend/task-editor.js','utf8');
const tick = () => new Promise(setImmediate);
function deferred() { let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject}; }
function fixture(saveText=async()=>{}) {
  const c=vm.createContext({window:{},id:'task-a',activeOperation:null,busy:false,busyKind:'',dirty:true,
    materials:{images:[]},readingDirty:false,needsWorkspaceReload:false,
    currentId:()=>c.id,current:id=>c.id===id,render(){},notify(){},saveText,
    saveIdeas:async()=>{},hooks:{reload:async()=>{}},canonicalText(){},requiresConfirmation(){return false;}});
  for (const name of ['waitForIdle','runOperation','perform']) {
    vm.runInContext(source.match(new RegExp(`  async function ${name}\\([^]*?\\n  \\}`))[0],c);
  }
  vm.runInContext(source.match(/  window.TaskInput = \{[^]*?\n  \};/)[0],c);
  return c;
}
test('next waits for an ongoing text save; text-only preparation never requests recognition',async()=>{
  const pending=deferred();let saves=0;const c=fixture(async()=>{if(++saves===1)await pending.promise;});
  const saving=c.window.TaskInput.saveDraft();await tick();
  let finished=false;const next=c.window.TaskInput.prepareAnalysis().then(()=>{finished=true;});
  await tick();assert.equal(finished,false);assert.equal(saves,1);assert.equal(c.busyKind,'text');
  pending.resolve();await Promise.all([saving,next]);assert.equal(finished,true);assert.equal(c.busy,false);
});
test('failed pending save blocks next and a later click can retry',async()=>{
  const pending=deferred();let saves=0;const c=fixture(async()=>{if(++saves===1)await pending.promise;});
  const saving=c.window.TaskInput.saveDraft();await tick();const next=c.window.TaskInput.prepareAnalysis();
  const a=assert.rejects(saving,/offline/),b=assert.rejects(next,/offline/);
  pending.reject(Error('offline'));await Promise.all([a,b]);assert.equal(c.busy,false);assert.equal(c.activeOperation,null);
  await c.window.TaskInput.prepareAnalysis();assert.equal(saves,2);
});
test('attachment operation is awaited before next saves text',async()=>{
  const pending=deferred();const order=[];const c=fixture(async()=>order.push('save'));
  const attachment=c.perform(async()=>{order.push('attachment');await pending.promise;order.push('uploaded');});
  await tick();const next=c.window.TaskInput.prepareAnalysis();await tick();assert.deepEqual(order,['attachment']);
  assert.equal(c.busyKind,'attachment');pending.resolve();await Promise.all([attachment,next]);
  assert.deepEqual(order,['attachment','uploaded','save']);
});
test('switching task during a save cannot advance or save the newly selected task',async()=>{
  const pending=deferred();let saves=0;const c=fixture(async()=>{saves++;await pending.promise;});
  const saving=c.window.TaskInput.saveDraft();await tick();const next=c.window.TaskInput.prepareAnalysis();
  const denied=assert.rejects(next,/已切换/);c.id='task-b';pending.resolve();await saving;await denied;assert.equal(saves,1);
});
test('concurrent save callers serialize instead of reporting an image error',async()=>{
  const pending=deferred();let saves=0,running=0,max=0;const c=fixture(async()=>{
    running++;max=Math.max(max,running);if(++saves===1)await pending.promise;running--;
  });
  const savesPending=[c.window.TaskInput.saveDraft(),c.window.TaskInput.saveDraft(),c.window.TaskInput.saveDraft()];
  await tick();assert.equal(saves,1);pending.resolve();await Promise.all(savesPending);assert.equal(max,1);assert.equal(saves,3);
});
