'use strict';
window.LearningSharing = (() => {
  let key = '', data = null, busy = false, serial = 0, context = null;
  const root = () => document.getElementById('learningSharing');
  const escape = value => String(value || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const request = (suffix = '', options) => window.PlatformAPI.request(`${key}${suffix}`, options);
  const message = text => { root().querySelector('[data-sharing-status]').textContent = text; };
  function render() {
    const host = root(); if (!host) return;
    host.hidden = !context?.teachingId || !context?.classroomId;
    if (host.hidden) return;
    const candidates = data?.candidates || [];
    const choices = candidates.map(c => `<option value="${escape(c.session_id)}">${escape(c.student_name)} · ${escape(c.level_name)}${c.task_version_number ? ` · 第${Number(c.task_version_number)}版` : ""}</option>`).join('');
    host.innerHTML = `<div class="card-heading"><div><h2>学生学习反馈</h2><p>推送思维反馈，并选择两份对话供本班已提交学生学习。</p></div><button class="button primary" data-push-thinking ${busy || !candidates.length ? 'disabled' : ''}>推送思维反馈</button></div>
      <details><summary>优秀对话示例推荐${data?.publications?.length ? ' · 已推送' : ''}</summary><div class="sharing-controls">
      ${[1,2].map(n => `<label>对话示例${n}<select data-example="${n}" ${busy?'disabled':''}><option value="">请选择学生</option>${choices}</select><button type="button" class="button" data-preview="${n}" ${busy?'disabled':''}>预览对话</button></label>`).join('')}
      <div><button class="button primary" data-push-examples ${busy || candidates.length < 2?'disabled':''}>推送两份示例</button><button class="button" data-withdraw-examples ${busy || !data?.publications?.length?'disabled':''}>撤回示例</button></div></div>
      <div class="sharing-preview" data-sharing-preview hidden></div></details><p class="muted" data-sharing-status role="status">${!data?'正在读取…':!candidates.length?'完成学生思维诊断后可推送。':'按思维结构由高到低排列，请先预览并确认示例。'}</p>`;
    const saved = data?.publications?.at(-1)?.session_ids;
    const pair = saved?.every(id => candidates.some(c => c.session_id === id)) ? saved : candidates.length > 1 ? [candidates[0].session_id, candidates.find(c => c.session_id !== candidates[0].session_id && c.task_version_id === candidates[0].task_version_id)?.session_id] : [];
    [1,2].forEach(n => { host.querySelector(`[data-example="${n}"]`).value = pair[n-1] || ''; });
  }
  async function load(teachingId, classroomId, revision = '') {
    const next = teachingId && classroomId ? `/api/precision-teachings/${encodeURIComponent(teachingId)}/classrooms/${encodeURIComponent(classroomId)}/learning-sharing` : '';
    if (next === key && context?.revision === revision) return;
    const token = ++serial;
    key = next; context = {teachingId, classroomId, revision}; data = null; busy = false; render();
    if (!key) return;
    try { const value = await request(); if (token !== serial) return; data = value; render(); }
    catch(error) { if (token === serial) { message(`读取失败：${error.message}`); key = ''; } }
  }
  root()?.addEventListener('click', async event => {
    const button = event.target.closest('button'); if (!button || busy || !data) return;
    const token = serial;
    const selected = [1,2].map(n => root().querySelector(`[data-example="${n}"]`).value);
    if (button.hasAttribute('data-push-examples') && (selected.some(id => !id) || selected[0] === selected[1])) { message('请选择两名不同学生的对话。'); return; }
    busy = true; root().querySelectorAll('button,select').forEach(el => el.disabled = true); message('正在处理…');
    try {
      if (button.hasAttribute('data-preview')) {
        const id = selected[Number(button.dataset.preview)-1]; if (!id) throw new Error('请先选择学生');
        const value = await request(`/dialogues/${encodeURIComponent(id)}`);
        if (token !== serial) return;
        const preview = root().querySelector('[data-sharing-preview]'); preview.hidden = false;
        preview.innerHTML = value.messages.map(m => `<div class="sharing-message ${m.role==='student'?'student':'ai'}"><b>${m.role==='student'?'学生':'AI'}</b><p>${escape(m.content)}</p></div>`).join('');
        window.TaskMath?.typeset(preview); message('请核对以上对话是否适合推荐。');
      } else if (button.hasAttribute('data-push-thinking')) {
        const value = await request('/thinking-feedback', {method:'POST'});
        if (token !== serial) return;
        message(`已向 ${value.pushed_count} 名学生推送思维反馈。`);
      } else {
        const result = button.hasAttribute('data-push-examples')
          ? await request('/examples', {method:'POST', body:JSON.stringify({session_ids:selected})})
          : await request('/examples', {method:'DELETE'});
        if (token !== serial) return;
        data = result; busy = false; render(); message(button.hasAttribute('data-push-examples')?'已推送两份对话示例。':'已撤回对话示例。');
      }
    } catch(error) { if (token === serial) message(`操作失败：${error.message}`); }
    finally {
      if (token === serial) {
        busy = false;
        root().querySelectorAll('button,select').forEach(el => el.disabled = false);
        root().querySelector('[data-push-examples]').disabled = data.candidates.length < 2;
        root().querySelector('[data-withdraw-examples]').disabled = !data.publications.length;
        root().querySelector('[data-push-thinking]').disabled = !data.candidates.length;
      }
    }
  });
  return {load};
})();
