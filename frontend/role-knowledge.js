/* Teacher-only role references. Never attach these files to student tasks. */
(() => {
  let hooks, state = null, busy = false;
  const byId = id => document.getElementById(id);
  const currentId = () => hooks?.getContext()?.teachingId;
  const update = (next, id) => {
    if (currentId() !== id) return;
    state = next; hooks.onChange(next); render();
  };
  function render() {
    const documents = state?.documents || [];
    byId('uploadRoleKnowledge').disabled = busy || !currentId() || documents.length >= 5;
    byId('uploadRoleKnowledge').textContent = busy ? '正在处理…' : '＋ 上传知识库';
    byId('roleKnowledgeStatus').textContent = busy ? '正在保存…' : state?.pending_publish
      ? '已保存，重新发布后生效。'
      : documents.length ? `已上传 ${documents.length} 份资料`
      : 'PDF / DOCX / TXT，最多5份，每份5MB。';
    const list = byId('roleKnowledgeList'); list.replaceChildren();
    for (const item of documents) {
      const row = document.createElement('li'), details = document.createElement('details');
      const summary = document.createElement('summary'), text = document.createElement('pre');
      summary.textContent = `${item.name} · ${item.characters} 字 · 查看提取内容`;
      text.textContent = item.text;
      details.append(summary, text);
      const remove = document.createElement('button');
      remove.type = 'button'; remove.className = 'button small'; remove.textContent = '移除'; remove.disabled = busy;
      remove.setAttribute('aria-label', `移除知识库 ${item.name}`);
      remove.title = '从待发布知识库移除，历史发布版本保留';
      remove.addEventListener('click', () => removeDocument(item.id));
      row.append(details, remove); list.append(row);
    }
  }
  async function upload(files) {
    if (busy || !currentId()) return;
    const selected = [...files]; if (!selected.length) return;
    if (selected.length + (state?.documents?.length || 0) > 5) { hooks.notify('每个任务最多上传5份角色知识库'); return; }
    if (selected.some(file => !/\.(pdf|docx|txt)$/i.test(file.name))) { hooks.notify('知识库支持 PDF、Word（.docx）和 TXT'); return; }
    if (selected.some(file => file.size > 5 * 1024 * 1024)) { hooks.notify('每份知识库文件请控制在5MB以内'); return; }
    const id = currentId(); busy = true; render();
    try {
      for (const file of selected) {
        if (currentId() !== id) break;
        const next = await PlatformAPI.request(`/api/precision-teachings/${encodeURIComponent(id)}/role-knowledge`, {
          method: 'POST', headers: {'Content-Type':file.type || 'application/octet-stream','X-File-Name':encodeURIComponent(file.name),'X-Knowledge-Version':state.source_hash}, body:file
        });
        update(next, id);
      }
    } catch (error) { if (currentId() === id) hooks.notify(error.message); }
    finally { busy = false; render(); }
  }
  async function removeDocument(documentId) {
    if (busy || !currentId()) return;
    const id = currentId(); busy = true; render();
    try {
      const next = await PlatformAPI.request(`/api/precision-teachings/${encodeURIComponent(id)}/role-knowledge/${encodeURIComponent(documentId)}`,{
        method:'DELETE',body:JSON.stringify({source_hash:state.source_hash})
      });
      update(next,id);
    } catch (error) { if (currentId() === id) hooks.notify(error.message); }
    finally { busy = false; render(); }
  }
  byId('uploadRoleKnowledge').addEventListener('click',()=>byId('roleKnowledgeInput').click());
  byId('roleKnowledgeInput').addEventListener('change',event=>{upload(event.target.files);event.target.value='';});
  window.RoleKnowledge = {
    configure(value) {hooks=value;},
    applyWorkspace(workspace) {state=workspace.role_knowledge || {documents:[],source_hash:''};render();},
    prepare() {if (busy) throw new Error('知识库正在上传或移除，请完成后再发布任务');},
  };
})();
