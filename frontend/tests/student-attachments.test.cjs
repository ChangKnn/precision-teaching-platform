const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('frontend/student/student.js','utf8');
function fixture(){
 const nodes=[];const requests=[];
 const element=tag=>({tag,children:[],append(item){this.children.push(item)},replaceChildren(){this.children=[]},addEventListener(name,handler){this[name]=handler},click(){this.clicked=true}});
 const container=element('container');
 const c=vm.createContext({state:{token:'student-token'},taskImageUrls:[],imageLoadVersion:0,
  document:{getElementById:()=>container,createElement:element},URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},
  fetch:async(url,options)=>{requests.push({url,options});return {ok:true,blob:async()=>({})}},showToast(){}});
 vm.runInContext(source.match(/async function renderTaskImages\([^]*?\n\}/)[0],c);
 vm.runInContext(source.match(/async function appendTaskImage\([^]*?\n\}/)[0],c);
 return {c,container,requests};
}
test('published files, web links and images are visible and file access uses student authentication',async()=>{
 const {c,container,requests}=fixture();
 await c.renderTaskImages({task_attachments:[{kind:'file',name:'材料.pdf',file_url:'/private/file'}, {kind:'link',name:'网页材料',url:'https://example.org/'}],task_images:[{file_url:'/private/image'}]});
 assert.equal(container.children[0].textContent,'老师提供的任务附件');
 assert.match(container.children[1].textContent,/材料.pdf/);assert.equal(container.children[2].href,'https://example.org/');
 assert.equal(container.children[3].tag,'img');
 await container.children[1].click();
 assert.deepEqual(requests.map(r=>r.url),['/private/image','/private/file']);
 for(const request of requests)assert.equal(request.options.headers['X-Student-Token'],'student-token');
});
test('no empty attachment heading appears for a text-only task',async()=>{
 const {c,container}=fixture();await c.renderTaskImages({task_attachments:[],task_images:[]});assert.equal(container.children.length,0);
});
test('image files render immediately while documents remain downloadable',async()=>{
 const {c,container,requests}=fixture();
 await c.renderTaskImages({task_attachments:[{kind:'file',name:'材料.png',content_type:'image/png',file_url:'/private/picture'}, {kind:'file',name:'旧图片.jpg',file_url:'/private/old'}, {kind:'file',name:'题目.pdf',content_type:'application/pdf',file_url:'/private/pdf'}]});
 assert.deepEqual(container.children.map(item=>item.tag),['h3','img','img','button']);
 assert.equal(container.children[1].alt,'材料.png');
 assert.deepEqual(requests.map(r=>r.url),['/private/picture','/private/old']);
 await container.children[3].click();
 assert.equal(requests[2].url,'/private/pdf');
 for(const request of requests)assert.equal(request.options.headers['X-Student-Token'],'student-token');
});
