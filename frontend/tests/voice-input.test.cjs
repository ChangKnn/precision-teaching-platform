'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {pcm16le, Transcript} = require('../student/voice-input.js');

function environment({enabled = true, worklet = false, gumError = null, lateMic = false} = {}) {
  const listeners = {}, timers = new Map(), intervals = new Map(), sockets = [], contexts = [];
  let id = 0, resolveMic;
  const track = {stopped: false, stop() { this.stopped = true; }}, stream = {getTracks: () => [track]};
  const node = () => ({connect() {}, disconnect() { this.disconnected = true; }});
  class AudioContext {
    constructor() { this.sampleRate = 44100; this.state = 'suspended'; this.destination = {}; contexts.push(this);
      if (worklet) this.audioWorklet = {addModule: async () => {}}; }
    async resume() { this.state = 'running'; this.resumed = true; }
    async close() { this.state = 'closed'; }
    createMediaStreamSource() { return node(); }
    createGain() { return {...node(), gain: {value: 1}}; }
    createScriptProcessor() { this.processor = node(); return this.processor; }
  }
  class Worklet {
    constructor(ac) {
      Object.assign(this, node()); ac.worklet = this;
      this.port = {close() {}, postMessage: message => {
        if (message.type === 'stop') {
          this.port.onmessage?.({data: {type: 'pcm', buffer: pcm16le(new Float32Array([.5]))}});
          this.port.onmessage?.({data: {type: 'stopped'}});
        }
      }};
    }
  }
  class Socket {
    constructor(url) { this.url = url; this.readyState = 0; this.bufferedAmount = 0; this.sent = []; sockets.push(this); }
    open() { this.readyState = 1; this.onopen(); }
    event(value) { this.onmessage?.({data: JSON.stringify(value)}); }
    send(value) { this.sent.push(JSON.parse(value)); }
    close() { this.readyState = 3; this.closed = true; }
  }
  const label = {textContent: ''};
  const button = {disabled: false, classList: {toggle() {}}, attrs: {}, setAttribute(k,v) {this.attrs[k]=v;},
    querySelector: () => label, addEventListener(k,v) { this[k] = v; }};
  const input = {value: '已有：', selectionStart: 3, selectionEnd: 3, readOnly: false};
  const status = {textContent: ''}, errors = [], busy = [], requests = [];
  const context = {token: 'student-token', sessionId: 'session-1', versionId: 'v1', disabled: false};
  const window = {isSecureContext: true, AudioContext, AudioWorkletNode: worklet ? Worklet : undefined,
    location: {href: 'https://example.test/student/'},
    setTimeout(fn, ms) { const k=++id; timers.set(k,{fn,ms}); return k; }, clearTimeout(k) {timers.delete(k);},
    setInterval(fn) {const k=++id; intervals.set(k,fn); return k;}, clearInterval(k) {intervals.delete(k);},
    addEventListener(k,v) {listeners[k]=v;}};
  const document = {hidden: false, addEventListener(k,v) {listeners[k]=v;}};
  const navigator = {mediaDevices: {getUserMedia: async () => {
    if (gumError) throw Object.assign(new Error('denied'), {name:gumError});
    if (lateMic) await new Promise(resolve => {resolveMic=resolve;});
    return stream;
  }}};
  const scope=vm.createContext({window,document,navigator,WebSocket:Socket,URL,DataView,ArrayBuffer,Uint8Array,
    btoa: s=>Buffer.from(s,'binary').toString('base64')});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../student/voice-input.js'),'utf8'),scope);
  const controller=window.StudentVoiceInput.create({button,input,status,getContext:()=>context,
    request: async p=>{requests.push(p);return {enabled,max_seconds:180};},
    onBusyChange:v=>busy.push(v),onDraftChange() {},onError:e=>errors.push(e)});
  const result=(text,id=1,final=false)=>({header:{event:'result-generated'},payload:{output:{sentence:{sentence_id:id,text,sentence_end:final}}}});
  return {controller,window,document,context,button,input,status,errors,busy,requests,sockets,contexts,track,
    result,listeners,timers,resolveMic:()=>resolveMic?.(),async listen(){await controller.start();sockets[0].open();sockets[0].event({header:{event:'task-started'}});}};
}

test('PCM is clipped, signed 16-bit and explicitly little endian',()=>{
  assert.deepEqual([...new Uint8Array(pcm16le(new Float32Array([-2,-1,0,.5,1,2])))],
    [0,128,0,128,0,0,255,63,255,127,255,127]);
});

test('sentence replacement, out-of-order IDs, finals and heartbeats never duplicate text',()=>{
  const t=new Transcript();
  t.update({sentence_id:1,text:'你',sentence_end:false});t.update({sentence_id:1,text:'你好',sentence_end:false});
  t.update({sentence_id:2,text:'再见。',sentence_end:true});t.update({sentence_id:1,text:'你好。',sentence_end:true});
  t.update({sentence_id:1,text:'迟到的中间结果',sentence_end:false});
  t.update({sentence_id:0,text:'heartbeat',heartbeat:true});
  assert.equal(t.text,'你好。再见。');
});

test('actual rate, authenticated first frame and no audio before task-started',async()=>{
  const e=environment();await e.controller.start();const ws=e.sockets[0];ws.open();
  assert.equal(ws.url,'wss://example.test/api/student/asr/ws');assert.ok(!ws.url.includes('token'));
  assert.equal(ws.sent[0].sample_rate,44100);assert.equal(ws.sent[0].token,'student-token');
  const process=()=>e.contexts[0].processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array([-1,0,1])},outputBuffer:{getChannelData:()=>new Float32Array(3)}});
  process();assert.equal(ws.sent.length,1);
  ws.event({header:{event:'task-started'}});process();assert.equal(ws.sent.length,2);
  assert.deepEqual([...Buffer.from(ws.sent[1].audio,'base64')],[0,128,0,0,255,127]);
  e.controller.cancel();assert.ok(e.track.stopped);assert.equal(e.contexts[0].state,'closed');
});

test('stop releases mic, flushes tail PCM before finish, and waits for final results without sending chat',async()=>{
  const e=environment({worklet:true});await e.listen();const ws=e.sockets[0];
  ws.event(e.result('中间'));ws.event(e.result('完整'));
  assert.equal(e.input.value,'已有：完整');
  await e.controller.stop();assert.ok(e.controller.busy);assert.ok(e.input.readOnly);assert.ok(e.track.stopped);
  assert.ok(ws.sent.at(-2).audio);assert.deepEqual(ws.sent.at(-1),{type:'finish'});
  ws.event(e.result('完整尾句。',1,true));assert.equal(e.input.value,'已有：完整尾句。');
  ws.event({header:{event:'task-finished'}});assert.equal(e.controller.busy,false);assert.equal(e.input.readOnly,false);
  assert.deepEqual(e.requests,['/api/student/asr/config']);assert.ok(ws.closed);
  assert.equal(e.errors.length,0);
});

test('permission denial and missing key restore text entry without leaking resources',async()=>{
  for (const options of [{gumError:'NotAllowedError'},{enabled:false}]) {
    const e=environment(options);await e.controller.start();
    assert.equal(e.controller.busy,false);assert.equal(e.input.value,'已有：');assert.equal(e.input.readOnly,false);
    assert.equal(e.sockets.length,0);assert.equal(e.contexts[0].state,'closed');assert.ok(e.errors.length);
  }
});

test('cancelled pending microphone permission cannot resurrect recording or leak a late stream',async()=>{
  const e=environment({lateMic:true});const pending=e.controller.start();
  for(let i=0;i<8;i++)await Promise.resolve();
  e.controller.cancel();e.resolveMic();await pending;
  assert.ok(e.track.stopped);assert.equal(e.controller.busy,false);assert.equal(e.sockets.length,0);
});

test('network backlog aborts recording, keeps text and releases resources',async()=>{
  const e=environment();await e.listen();const ws=e.sockets[0];ws.event(e.result('已收到的文字'));
  ws.bufferedAmount=300*1024;
  e.contexts[0].processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(3)},outputBuffer:{getChannelData:()=>new Float32Array(3)}});
  assert.equal(e.controller.busy,false);assert.ok(e.track.stopped);assert.ok(ws.closed);
  assert.equal(e.input.value,'已有：已收到的文字');assert.ok(e.errors[0].includes('积压'));
});

test('finish timeout preserves latest partial text and restores editing',async()=>{
  const e=environment();await e.listen();e.sockets[0].event(e.result('最后已收到'));
  await e.controller.stop();const timeout=[...e.timers.values()].find(t=>t.ms===14000);assert.ok(timeout);timeout.fn();
  assert.equal(e.controller.busy,false);assert.equal(e.input.readOnly,false);assert.equal(e.input.value,'已有：最后已收到');
});

test('task switching, pagehide and backgrounding stop mic and ignore late transcript events',async()=>{
  for(const reason of ['task','pagehide','visibilitychange']) {
    const e=environment();await e.listen();const ws=e.sockets[0];const original=e.input.value;
    if(reason==='task'){e.context.sessionId='another';e.controller.refresh();}
    else {e.document.hidden=true;e.listeners[reason]();}
    ws.event(e.result('不应填入其他任务'));assert.equal(e.input.value,original);
    assert.ok(e.track.stopped);assert.ok(ws.closed);assert.equal(e.controller.busy,false);
  }
});

test('AudioWorklet downmixes mono, flushes partial chunk and stops processing',()=>{
  let Processor;const output=[];
  const scope=vm.createContext({sampleRate:48000,AudioWorkletProcessor:class {constructor(){this.port={postMessage:m=>output.push(m)};}},
    registerProcessor:(name,Class)=>{assert.equal(name,'student-pcm');Processor=Class;}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../student/pcm-worklet.js'),'utf8'),scope);
  const processor=new Processor();processor.process([[new Float32Array([-1,0,1]),new Float32Array([-1,0,1])]],[[new Float32Array(3)]]);
  assert.equal(output.length,0);processor.port.onmessage({data:{type:'stop'}});
  assert.deepEqual([...new Uint8Array(output[0].buffer)],[0,128,0,0,255,127]);
  assert.equal(output[1].type,'stopped');assert.equal(processor.process([],[]),false);
});
