'use strict';
window.LessonMaterials = (() => {
  let teachingId = null, state = null, dirty = false, busy = false, serial = 0;
  const el = id => document.getElementById(id);
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const path = id => `/api/precision-teachings/${encodeURIComponent(id)}/lesson-materials`;
  function render() {
    const documents = state?.documents || [];
    el('lessonMaterialsList').innerHTML = documents.map(d => `<li><a href="${escape(d.download_url)}">${escape(d.name)}</a><button type="button" class="text-button" data-remove-lesson-material="${escape(d.id)}" ${busy?'disabled':''}>移除</button></li>`).join('');
    el('lessonMaterialsReview').hidden = !documents.length;
    el('lessonMaterialsText').value = state?.text || '';
    el('lessonMaterialsStatus').textContent = busy ? '正在处理…' : state ? state.confirmed ? '已核对并保存' : documents.length ? '请核对提取内容' : '尚未上传' : '正在读取…';
    el('lessonMaterialsFile').disabled = busy || !state;
    el('confirmLessonMaterials').disabled = busy;
    el('lessonMaterialsText').disabled = busy;
  }
  async function load(id) {
    if (!id || (teachingId === id && state)) return;
    const token = ++serial;
    teachingId = id; state = null; dirty = false; busy = false; render();
    try {
      const data = await window.PlatformAPI.request(path(id));
      if (token !== serial) return;
      state = data; render();
    } catch (error) {
      if (token === serial) el('lessonMaterialsStatus').textContent = `读取失败：${error.message}`;
    }
  }
  async function mutate(operation) {
    if (busy || !state) return;
    const id = teachingId, token = serial;
    busy = true;
    el('lessonMaterialsStatus').textContent = '正在处理…';
    el('lessonMaterialsFile').disabled = true;
    el('confirmLessonMaterials').disabled = true;
    el('lessonMaterialsText').disabled = true;
    try {
      const data = await operation(id);
      if (token !== serial) return;
      state = data; dirty = false; render();
      window.dispatchEvent(new CustomEvent('lesson-materials-changed', {detail:{teachingId:id}}));
    } catch (error) {
      if (token === serial) {
        el('lessonMaterialsStatus').textContent = `操作失败：${error.message}`;
        window.showToast?.(error.message);
      }
    } finally {
      if (token === serial) {
        busy = false;
        el('lessonMaterialsFile').disabled = false;
        el('confirmLessonMaterials').disabled = false;
        el('lessonMaterialsText').disabled = false;
        if (state && !dirty && !el('lessonMaterialsStatus').textContent.startsWith('操作失败')) render();
      }
    }
  }
  document.addEventListener('change', event => {
    if (event.target.id !== 'lessonMaterialsFile') return;
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (file.size > 10*1024*1024) { el('lessonMaterialsStatus').textContent='单份资料最多10MB'; return; }
    if (dirty) { el('lessonMaterialsStatus').textContent='请先保存下方修改，再上传其他文件'; return; }
    mutate(id => window.PlatformAPI.request(path(id), {method:'POST',headers:{'Content-Type':'application/octet-stream','X-File-Name':encodeURIComponent(file.name),'X-Material-Version':state.revision},body:file}));
  });
  document.addEventListener('input', event => {
    if (event.target.id === 'lessonMaterialsText') {
      dirty = true; el('lessonMaterialsStatus').textContent='有未保存的核对内容';
    }
  });
  document.addEventListener('click', event => {
    if (event.target.closest('#confirmLessonMaterials')) {
      mutate(id => window.PlatformAPI.request(path(id), {method:'PUT',body:JSON.stringify({revision:state.revision,text:el('lessonMaterialsText').value})}));
    }
    const remove = event.target.closest('[data-remove-lesson-material]');
    if (remove) {
      if (dirty) { el('lessonMaterialsStatus').textContent='请先保存下方修改，再移除文件'; return; }
      mutate(id => window.PlatformAPI.request(path(id)+'/'+encodeURIComponent(remove.dataset.removeLessonMaterial), {method:'DELETE',body:JSON.stringify({revision:state.revision})}));
    }
  });
  window.addEventListener('beforeunload', event => {
    if (dirty || busy) { event.preventDefault(); event.returnValue=''; }
  });
  return {load, prepare() {
    if (busy) throw new Error('教学资料正在处理，请稍候');
    if (dirty || (state?.documents?.length && !state.confirmed)) throw new Error('请先核对并保存“当前教学设计方案”的内容');
  }};
})();
