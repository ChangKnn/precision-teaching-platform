/* Image and formula authoring. Source images never substitute unverified text. */
(() => {
  let hooks = null;
  let materials = null;
  let busy = false;
  let locallyChanged = false;
  let formulaRange = [0, 0];
  let recognitionVersion = '';
  let workspaceId = null;
  const byId = id => document.getElementById(id);
  const text = () => byId('diagnosisTaskText').value.trim();
  const notes = () => byId('taskGeometryNotes').value.trim();
  const context = () => hooks?.getContext();
  const notify = message => hooks?.notify(message);
  const isCurrent = id => context()?.teachingId === id;
  const requiresConfirmation = () => Boolean((materials?.confirmation_required || notes()) && (!materials?.confirmed || locallyChanged));

  function canonicalText() {
    return text() + (notes() ? '\n\n【题图条件与补充说明（教师已核对）】\n' + notes() : '');
  }

  function renderImages(container, editable) {
    const key = JSON.stringify([materials?.images, materials?.locked, busy, editable]);
    if (container.dataset.materialKey === key) return;
    container.dataset.materialKey = key;
    container.replaceChildren();
    for (const [index, image] of (materials?.images || []).entries()) {
      const figure = document.createElement('figure');
      const link = document.createElement('a');
      link.href = image.file_url;
      link.target = '_blank';
      link.rel = 'noopener';
      const img = document.createElement('img');
      img.src = image.file_url;
      img.alt = `题图 ${index + 1}，点击查看原图`;
      link.append(img);
      figure.append(link);
      const caption = document.createElement('figcaption');
      caption.textContent = `题图 ${index + 1} · 点击查看原图`;
      if (editable && !materials.locked) {
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'text-button';
        remove.textContent = '移除';
        remove.disabled = busy;
        remove.addEventListener('click', () => perform(async id => {
          await saveTextAndNotes(id);
          const response = await PlatformAPI.request(`/api/precision-teachings/${id}/materials/images/${image.id}`, {
            method: 'DELETE', body: JSON.stringify({ source_hash: materials.source_hash }),
          });
          if (isCurrent(id)) { update(response); await hooks.reload(id); }
        }));
        caption.append(remove);
      }
      figure.append(caption);
      container.append(figure);
    }
  }

  function render() {
    const required = requiresConfirmation();
    const status = byId('taskMaterialStatus');
    status.textContent = busy ? '正在处理…' : required ? '题目材料待核对' : materials?.confirmed ? '题目材料已核对' : '可输入文字、插入公式，或粘贴题目截图';
    status.className = required ? 'task-material-status pending' : 'task-material-status';
    byId('taskToolsBadge').textContent = required ? ' · 材料待核对' : materials?.images?.length ? ` · 已附 ${materials.images.length} 张图` : '';
    byId('taskImageUploadButton').disabled = busy || Boolean(materials?.locked);
    byId('recognizeTaskImages').disabled = busy || !materials?.images?.length || !materials?.recognition_available || Boolean(materials?.locked);
    byId('confirmTaskMaterials').disabled = busy || !text();
    byId('taskGeometryNotes').readOnly = Boolean(materials?.locked);
    byId('taskImageDropzone').classList.toggle('locked', Boolean(materials?.locked));
    byId('taskRecognitionHint').textContent = materials?.locked
      ? '本任务已有学生开始作答。如需更换题图或图形条件，请新建精准教学。'
      : materials?.recognition_available ? '上传后可识别题干与公式；请对照原图核对，补全图中必要条件。' : 'AI 图片识别尚未启用。可保留题图，手动填写题干、公式和图形条件后核对。';
    renderImages(byId('taskImageGallery'), true);
    TaskMath.render(byId('taskTextRenderedPreview'), text(), openFormula);
    byId('taskRenderedPreview').hidden = !text();
  }

  function update(state) {
    materials = state;
    hooks?.onMaterials(state);
    render();
  }

  async function perform(operation) {
    const id = context()?.teachingId;
    if (!id || busy) return;
    busy = true;
    render();
    try { await operation(id); }
    catch (error) { if (isCurrent(id)) notify(error.message); }
    finally { busy = false; if (isCurrent(id)) render(); }
  }

  async function saveTextAndNotes(id) {
    const draftNotes = notes();
    await hooks.saveDraft();
    if (!isCurrent(id)) throw new Error('当前精准教学已切换，请在原项目中继续');
    const state = await PlatformAPI.request(`/api/precision-teachings/${id}/materials`);
    const saved = await PlatformAPI.request(`/api/precision-teachings/${id}/materials`, {
      method: 'PUT', body: JSON.stringify({ geometry_notes: draftNotes, source_hash: state.source_hash }),
    });
    if (!isCurrent(id)) return;
    byId('taskGeometryNotes').value = draftNotes;
    update(saved);
    locallyChanged = false;
  }

  async function upload(files) {
    byId('taskOptionalTools').open = true;
    if (materials?.locked) { notify('本任务已有学生开始作答，请新建精准教学添加题图'); return; }
    const selected = [...files].filter(file => ['image/png', 'image/jpeg', 'image/webp'].includes(file.type));
    if (!selected.length) { notify('请选择 PNG、JPG 或 WEBP 格式的题目截图'); return; }
    if (selected.some(file => file.size > 5 * 1024 * 1024)) { notify('每张题图请控制在 5 MB 以内'); return; }
    if (selected.length + (materials?.images?.length || 0) > 4) { notify('每个任务最多保留 4 张题图'); return; }
    await perform(async id => {
      await saveTextAndNotes(id);
      for (const file of selected) {
        if (!isCurrent(id)) return;
        const state = await PlatformAPI.request(`/api/precision-teachings/${id}/materials/images`, {
          method: 'POST', headers: { 'Content-Type': file.type, 'X-Material-Version': materials.source_hash }, body: file,
        });
        update(state);
      }
      await hooks.reload(id);
    });
  }

  function markChanged() {
    locallyChanged = true;
    render();
  }

  function openFormula(latex = '', start = null, end = null) {
    const editor = byId('diagnosisTaskText');
    formulaRange = [start ?? editor.selectionStart, end ?? editor.selectionEnd];
    const selected = editor.value.slice(...formulaRange);
    const field = byId('taskFormulaField');
    field.value = latex || selected.replace(/^\\\(|\\\)$/g, '');
    byId('taskFormulaDialog').showModal();
    field.focus();
    if (window.mathVirtualKeyboard) {
      mathVirtualKeyboard.container = byId('taskFormulaKeyboard');
      mathVirtualKeyboard.show();
    }
  }

  byId('taskInsertFormula').addEventListener('click', () => openFormula());
  byId('taskFormulaCancel').addEventListener('click', () => byId('taskFormulaDialog').close());
  byId('taskFormulaDialog').addEventListener('close', () => {
    if (window.mathVirtualKeyboard) { mathVirtualKeyboard.hide(); mathVirtualKeyboard.container = document.body; }
  });
  byId('taskFormulaInsert').addEventListener('click', () => {
    const latex = byId('taskFormulaField').value.trim();
    if (!latex) return;
    const editor = byId('diagnosisTaskText');
    editor.setRangeText('\\(' + latex + '\\)', ...formulaRange, 'end');
    byId('taskFormulaDialog').close();
    editor.dispatchEvent(new Event('input', { bubbles: true }));
    byId('taskRenderedPreview').open = true;
    editor.focus();
    markChanged();
  });
  document.querySelectorAll('[data-task-formula]').forEach(button => button.addEventListener('click', () => {
    const field = byId('taskFormulaField');
    field.focus();
    field.insert(button.dataset.taskFormula);
  }));
  byId('taskImageUploadButton').addEventListener('click', () => byId('taskImageInput').click());
  byId('taskImageInput').addEventListener('change', event => { upload(event.target.files); event.target.value = ''; });
  const paste = event => {
    const images = [...(event.clipboardData?.items || [])].filter(item => item.kind === 'file' && item.type.startsWith('image/')).map(item => item.getAsFile()).filter(Boolean);
    if (images.length) { event.preventDefault(); upload(images); }
  };
  byId('diagnosisTaskText').addEventListener('paste', paste);
  byId('taskImageDropzone').addEventListener('paste', paste);
  byId('taskImageDropzone').addEventListener('dragover', event => { event.preventDefault(); });
  byId('taskImageDropzone').addEventListener('drop', event => { event.preventDefault(); upload(event.dataTransfer.files); });
  byId('diagnosisTaskText').addEventListener('input', markChanged);
  byId('taskGeometryNotes').addEventListener('input', markChanged);
  byId('confirmTaskMaterials').addEventListener('click', () => perform(async id => {
    await saveTextAndNotes(id);
    if (!isCurrent(id)) return;
    const state = await PlatformAPI.request(`/api/precision-teachings/${id}/materials/confirm`, {
      method: 'POST', body: JSON.stringify({ source_hash: materials.source_hash }),
    });
    update(state);
    await hooks.reload(id);
  }));
  byId('recognizeTaskImages').addEventListener('click', () => perform(async id => {
    const response = await PlatformAPI.request(`/api/precision-teachings/${id}/materials/recognize`, { method: 'POST' });
    if (!isCurrent(id)) return;
    recognitionVersion = response.source_hash;
    byId('recognizedTaskText').value = response.result.task_text;
    byId('recognizedGeometryNotes').value = response.result.geometry_notes;
    const list = byId('recognizedUncertainties');
    list.replaceChildren();
    for (const item of response.result.uncertainties) { const li = document.createElement('li'); li.textContent = item; list.append(li); }
    if (!list.childNodes.length) { const li = document.createElement('li'); li.textContent = '请仍对照原图核对符号、条件和图形标注。'; list.append(li); }
    byId('taskRecognitionDialog').showModal();
  }));
  byId('cancelTaskRecognition').addEventListener('click', () => byId('taskRecognitionDialog').close());
  byId('applyTaskRecognition').addEventListener('click', () => {
    if (materials?.source_hash !== recognitionVersion) { notify('题图已变化，请重新识别'); return; }
    const recognized = byId('recognizedTaskText').value.trim();
    if (!recognized) { notify('请先核对并填写识别出的题目'); return; }
    const editor = byId('diagnosisTaskText');
    editor.value = [editor.value.trim(), '【题目材料】\n' + recognized].filter(Boolean).join('\n\n');
    const geometry = byId('recognizedGeometryNotes').value.trim();
    if (geometry) byId('taskGeometryNotes').value = [notes(), geometry].filter(Boolean).join('\n');
    editor.dispatchEvent(new Event('input', { bubbles: true }));
    byId('taskRecognitionDialog').close();
    markChanged();
  });

  window.TaskInput = {
    configure(value) { hooks = value; },
    applyWorkspace(workspace) {
      const nextId = context()?.teachingId;
      if (nextId !== workspaceId) {
        byId('taskOptionalTools').open = false;
        byId('taskRenderedPreview').open = false;
        workspaceId = nextId;
      }
      materials = workspace.task_materials || null;
      byId('taskGeometryNotes').value = materials?.geometry_notes || '';
      locallyChanged = false;
      render();
    },
    requiresConfirmation,
    canonicalText,
    refreshPreview: render,
    async saveDraft() {
      if (busy) throw new Error('图片正在处理，请稍后保存');
      const id = context()?.teachingId;
      busy = true;
      try { await saveTextAndNotes(id); await hooks.reload(id); }
      finally { busy = false; render(); }
    },
    renderPublish() {
      TaskMath.render(byId('publishTaskPreviewText'), canonicalText() || '尚未填写任务内容，请返回诊断任务步骤补充。');
      renderImages(byId('publishTaskImages'), false);
    },
  };
})();
