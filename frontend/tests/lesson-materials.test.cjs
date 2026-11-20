const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../lesson-materials.js'),'utf8');
function harness(request) {
  const elements=new Map(),listeners=new Map();
  const el=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',innerHTML:'',disabled:false,hidden:false});return elements.get(id);};
  const window={PlatformAPI:{request},addEventListener(){},dispatchEvent(){}};
  vm.runInNewContext(source,{window,document:{getElementById:el,addEventListener:(type,fn)=>listeners.set(type,fn)},CustomEvent:class{}});
  return {api:window.LessonMaterials,el,listeners};
}
const saved={documents:[{id:'d',name:'课件<script>.pptx',download_url:'/private'}],text:'保留教师教学主线',confirmed:true,revision:'v1'};
test('reopening restores reviewed lesson text and escapes uploaded filenames',async()=>{
  const h=harness(async()=>saved);
  await h.api.load('t');
  assert.equal(h.el('lessonMaterialsText').value,saved.text);
  assert.equal(h.el('lessonMaterialsStatus').textContent,'已核对并保存');
  assert.match(h.el('lessonMaterialsList').innerHTML,/&lt;script&gt;/);
  assert.doesNotThrow(()=>h.api.prepare());
  h.listeners.get('input')({target:{id:'lessonMaterialsText'}});
  assert.throws(()=>h.api.prepare(),/核对并保存/);
});
test('late response from another task cannot replace current lesson materials',async()=>{
  let finish;
  const h=harness(id=>id.includes('/first/')?new Promise(resolve=>{finish=resolve;}):Promise.resolve({...saved,text:'新任务内容'}));
  const pending=h.api.load('first');
  await h.api.load('second');
  finish(saved);await pending;
  assert.equal(h.el('lessonMaterialsText').value,'新任务内容');
});
test('unreviewed upload blocks design and failed reads remain visible',async()=>{
  const h=harness(async()=>({...saved,confirmed:false}));
  await h.api.load('t');
  assert.throws(()=>h.api.prepare(),/核对并保存/);
  const bad=harness(async()=>{throw new Error('offline');});
  await bad.api.load('t');
  assert.match(bad.el('lessonMaterialsStatus').textContent,/读取失败：offline/);
});
