/* One complete task, with atomic editable formulas and optional image attachments. */
(() => {
  let hooks, materials = null, busy = false, changed = false, savedRange = null, editingFormula = null;
  let dirty = false, readingDirty = false, polling = null, workspaceId = null, readingKey = '', readingImageHash = '';
  let needsWorkspaceReload = false;
  let savedIdeas = '';
  const el = id => document.getElementById(id);
  const editor = el('diagnosisTaskEditor');
  const backing = el('diagnosisTaskText');
  const currentId = () => hooks?.getContext()?.teachingId;
  const notify = message => hooks?.notify(message);
  const current = id => currentId() === id;
  const requiresConfirmation = () => Boolean(materials?.images?.length && (readingDirty || (materials.confirmation_required && (!materials.confirmed || changed))));
  function serialize(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent.replace(/\u200b/g, '');
    if (node.nodeType !== Node.ELEMENT_NODE) return '';
    if (node.classList.contains('math-expression')) {
      const value = node.querySelector('math-field')?.value || '';
      return node.classList.contains('math-display') ? '\\[' + value + '\\]' : '\\(' + value + '\\)';
    }
    if (node.tagName === 'BR') return '\n';
    let result = '';
    for (const child of node.childNodes) {
      const block = child.nodeType === 1 && ['DIV', 'P'].includes(child.tagName);
      if (block && result && !result.endsWith('\n')) result += '\n';
      result += serialize(child);
      if (block && child.nextSibling && !result.endsWith('\n')) result += '\n';
    }
    return result;
  }
  function sync() { backing.value = serialize(editor); changed = true; dirty = true; backing.dispatchEvent(new Event('input', {bubbles:true})); }
  function decorate() {
    editor.querySelectorAll('.math-expression').forEach(node => {
      node.contentEditable = 'false'; node.tabIndex = 0; node.setAttribute('role', 'button');
      node.setAttribute('aria-label', '编辑公式'); node.title = '点击编辑公式';
    });
  }
  function loadEditor() {
    if (serialize(editor) !== backing.value) { TaskMath.render(editor, backing.value); decorate(); savedRange = null; }
  }
  function rememberSelection() {
    const selection = window.getSelection();
    if (selection?.rangeCount && editor.contains(selection.getRangeAt(0).commonAncestorContainer)) savedRange = selection.getRangeAt(0).cloneRange();
  }
  document.addEventListener('selectionchange', rememberSelection);
  editor.addEventListener('input', sync);
  // Treat a rendered formula as one editable-text atom, including MathLive's
  // read-only shadow content and the zero-width caret marker inserted after it.
  function adjacentFormula(range, backward) {
    let node = range.startContainer, offset = range.startOffset;
    if (node.nodeType === Node.TEXT_NODE) {
      const remaining = backward ? node.textContent.slice(0, offset) : node.textContent.slice(offset);
      if (remaining.replace(/\u200b/g, '')) return null;
    } else {
      const child = node.childNodes[backward ? offset - 1 : offset];
      if (child) {
        const candidate = formulaAtEdge(child, backward);
        if (candidate !== undefined) return candidate;
        node = child;
      }
    }
    while (node && node !== editor) {
      if (node.nodeType === Node.ELEMENT_NODE && ['DIV','P','LI'].includes(node.tagName)) return null;
      let sibling = backward ? node.previousSibling : node.nextSibling;
      while (sibling) {
        const candidate = formulaAtEdge(sibling, backward);
        if (candidate !== undefined) return candidate;
        sibling = backward ? sibling.previousSibling : sibling.nextSibling;
      }
      node = node.parentNode;
    }
    return null;
  }
  function formulaAtEdge(node, backward) {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent.replace(/\u200b/g, '') ? null : undefined;
    if (node.nodeType !== Node.ELEMENT_NODE) return undefined;
    if (node.classList.contains('math-expression')) return node;
    if (['BR','DIV','P','LI','TABLE'].includes(node.tagName)) return null;
    for (const child of backward ? [...node.childNodes].reverse() : node.childNodes) {
      const candidate = formulaAtEdge(child, backward);
      if (candidate !== undefined) return candidate;
    }
    return undefined;
  }
  let deletingFormula = false;
  function deleteFormula(event, backward) {
    if (deletingFormula || event.isComposing) return false;
    const selection = window.getSelection();
    const focused = event.target.closest?.('.math-expression');
    let range = selection?.rangeCount ? selection.getRangeAt(0).cloneRange() : null;
    if (focused && editor.contains(focused)) {
      range = document.createRange(); range.selectNode(focused);
    } else {
      if (!range || !editor.contains(range.commonAncestorContainer)) return false;
      if (range.collapsed) {
        const formula = adjacentFormula(range, backward);
        if (!formula) return false;
        range.selectNode(formula);
      } else {
        const formulas = [...editor.querySelectorAll('.math-expression')].filter(node => range.intersectsNode(node));
        if (!formulas.length) return false;
        for (const formula of formulas) {
          if (formula.contains(range.startContainer)) range.setStartBefore(formula);
          if (formula.contains(range.endContainer)) range.setEndAfter(formula);
        }
      }
    }
    event.preventDefault(); event.stopPropagation();
    editor.focus(); selection.removeAllRanges(); selection.addRange(range);
    deletingFormula = true;
    try {
      // Native editing keeps formula deletion in the browser's undo history.
      if (!document.execCommand('delete')) {
        range.deleteContents(); range.collapse(true);
        selection.removeAllRanges(); selection.addRange(range);
      }
      rememberSelection(); sync();
    } finally { deletingFormula = false; }
    return true;
  }
  editor.addEventListener('keydown', event => {
    if (!event.ctrlKey && !event.metaKey && !event.altKey && ['Backspace','Delete'].includes(event.key)) {
      deleteFormula(event, event.key === 'Backspace');
    }
  }, true);
  editor.addEventListener('beforeinput', event => {
    if (['deleteContentBackward','deleteContentForward'].includes(event.inputType)) deleteFormula(event, event.inputType === 'deleteContentBackward');
  });
  function openFormula(node = null) {
    rememberSelection(); editingFormula = node;
    el('taskFormulaTitle').textContent = node ? '编辑数学公式' : '插入数学公式';
    el('taskFormulaField').value = node?.querySelector('math-field')?.value || '';
    el('taskFormulaDialog').showModal(); el('taskFormulaField').focus();
    if (window.mathVirtualKeyboard) { mathVirtualKeyboard.container = el('taskFormulaKeyboard'); mathVirtualKeyboard.show(); }
  }
  editor.addEventListener('click', event => {
    const node = event.target.closest('.math-expression');
    if (node) { event.preventDefault(); event.stopPropagation(); openFormula(node); }
  }, true);
  editor.addEventListener('keydown', event => {
    const node = event.target.closest('.math-expression');
    if (node && ['Enter', ' '].includes(event.key)) { event.preventDefault(); openFormula(node); }
  });
  el('taskInsertFormula').addEventListener('click', () => openFormula());
  el('taskFormulaCancel').addEventListener('click', () => el('taskFormulaDialog').close());
  el('taskFormulaDialog').querySelector('p').textContent = '使用数学键盘输入公式，也可粘贴 LaTeX。确认后作为完整公式块放入任务，点击公式可再次编辑。';
  el('taskFormulaDialog').addEventListener('close', () => {
    if (window.mathVirtualKeyboard) { mathVirtualKeyboard.hide(); mathVirtualKeyboard.container = document.body; }
  });
  function insertNode(node) {
    editor.focus();
    const range = savedRange && editor.contains(savedRange.commonAncestorContainer) ? savedRange : document.createRange();
    if (range !== savedRange) { range.selectNodeContents(editor); range.collapse(false); }
    range.deleteContents(); range.insertNode(node); range.setStartAfter(node); range.collapse(true);
    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); savedRange = range.cloneRange();
  }
  el('taskFormulaInsert').addEventListener('click', () => {
    const latex = el('taskFormulaField').value.trim();
    if (!latex) return;
    if (editingFormula && editor.contains(editingFormula)) editingFormula.querySelector('math-field').value = latex;
    else {
      const holder = document.createElement('span'); TaskMath.render(holder, '\\(' + latex + '\\)');
      const formula = holder.querySelector('.math-expression'); insertNode(formula); decorate();
      insertNode(document.createTextNode('\u200b'));
    }
    el('taskFormulaDialog').close(); editor.focus(); sync();
  });
  document.querySelectorAll('[data-task-formula]').forEach(button => button.addEventListener('click', () => {
    el('taskFormulaField').focus(); el('taskFormulaField').insert(button.dataset.taskFormula);
  }));
  function canonicalText(includeTeacherNotes = true) {
    let result = backing.value.trim() + (includeTeacherNotes && materials?.geometry_notes ? '\n\n【题图条件与补充说明（教师已核对）】\n' + materials.geometry_notes : '');
    const reading = materials?.recognition;
    if (materials?.images?.length && reading?.status === 'ready') {
      result += '\n\n【图片附件内容（任务材料，非学生作答）】\n' + reading.task_text;
      if (reading.diagram_notes) result += '\n【图形、图表与材料标注】\n' + reading.diagram_notes;
    }
    return result;
  }
  function renderImages(container, editable) {
    const key = JSON.stringify([materials?.images, busy, editable]);
    if (container.dataset.materialKey === key) return;
    container.dataset.materialKey = key; container.replaceChildren();
    for (const [index, image] of (materials?.images || []).entries()) {
      const figure = document.createElement('figure'), link = document.createElement('a'), img = document.createElement('img');
      link.href = image.file_url; link.target = '_blank'; link.rel = 'noopener';
      img.src = image.file_url; img.alt = `图片附件 ${index + 1}，点击查看原图`; link.append(img); figure.append(link);
      const caption = document.createElement('figcaption'); caption.textContent = `图片附件 ${index + 1} · 查看原图`;
      if (editable) {
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'text-button'; remove.textContent = '移除'; remove.disabled = busy;
        remove.addEventListener('click', () => perform(async id => {
          await saveText(id);
          const state = await PlatformAPI.request(`/api/precision-teachings/${id}/materials/images/${image.id}`, {method:'DELETE',body:JSON.stringify({source_hash:materials.source_hash})});
          if (current(id)) { update(state); await hooks.reload(id); }
        })); caption.append(remove);
      }
      figure.append(caption); container.append(figure);
    }
  }
  function render() {
    el('taskMaterialStatus').textContent = busy ? '正在保存…' : materials?.images?.length ? `已附 ${materials.images.length} 张图片` : '';
    el('taskImageUploadButton').disabled = busy;
    renderImages(el('taskImageGallery'), true);
    renderAttachments(el('taskFileGallery'),true);
    renderReading();
  }
  function renderAttachments(container,editable) {
    if (!container) return;
    container.replaceChildren();
    for (const item of materials?.attachments || []) {
      const row=document.createElement('div');row.className='task-file-row';
      const link=document.createElement('a');link.href=item.kind==='link' ? item.url : item.file_url;
      link.target='_blank';link.rel='noopener noreferrer';link.textContent=`${item.kind==='link' ? '网页' : '文件'} · ${item.name}`;
      row.append(link);
      if (editable) {
        const remove=document.createElement('button');remove.type='button';remove.className='text-button';remove.textContent='移除';remove.disabled=busy;
        remove.addEventListener('click',()=>perform(async id=>{
          await saveText(id);
          update(await PlatformAPI.request(`/api/precision-teachings/${id}/materials/attachments/${item.id}`,{method:'DELETE',body:JSON.stringify({source_hash:materials.source_hash})}));
          await hooks.reload(id);
        }));row.append(remove);
      }
      container.append(row);
    }
  }
  function renderReading() {
    const hasImages = Boolean(materials?.images?.length), r = materials?.recognition || {status:'pending'};
    el('taskReadingPanel').hidden = !hasImages;
    if (!hasImages) {
      readingDirty = false; readingKey = ''; readingImageHash = '';
      clearTimeout(polling); polling = null;
      el('taskReadingText').value = '';
      return;
    }
    const labels = {none:'尚未识别',pending:'附件待识别',processing:'正在识别图片内容…',ready:r.reviewed ? '附件内容已核对' : '图片内容已识别',review:'识别完成，有内容需要核对',failed:'识别未完成',disabled:'尚未启用图片识别'};
    const unavailable = r.status === 'pending' && !materials.recognition_available;
    el('taskReadingStatus').textContent = readingDirty ? '附件内容已修改，尚未保存' : unavailable ? '尚未启用图片识别' : labels[r.status] || '附件待处理';
    el('taskReadingMessage').textContent = r.error || (unavailable ? '当前未启用视觉模型，可展开手动填写附件内容，核对后继续；不会伪造识别结果。' : r.status === 'ready' ? '后续分析将使用这些附件内容；可展开查看，如发现错误可修改。' : r.status === 'processing' ? '原任务和图片已保存，识别完成后会自动更新。' : '可展开查看识别内容、补全疑点；不会用缺失的图片内容生成标准。');
    el('retryTaskReading').disabled = busy || r.status === 'processing' || !materials.recognition_available;
    el('retryTaskReading').textContent = r.status === 'pending' ? '开始识别' : '重新识别';
    el('saveTaskReading').disabled = busy || r.status === 'processing';
    el('taskReadingText').readOnly = r.status === 'processing';
    const key = JSON.stringify([workspaceId, r, materials.image_hash]);
    if (!readingDirty && readingKey !== key) {
      el('taskReadingText').value = [r.task_text, r.diagram_notes].filter(value=>value?.trim()).join('\n\n');
      const list = el('taskReadingUncertainties'); list.replaceChildren();
      for (const issue of r.uncertainties || []) { const li=document.createElement('li');li.textContent=issue;list.append(li); }
      readingKey = key; readingImageHash = materials.image_hash;
    }
    if (r.status === 'processing' && !polling) {
      const id = currentId();
      polling = setTimeout(async () => {
        polling = null;
        if (!current(id)) return;
        try { update(await PlatformAPI.request(`/api/precision-teachings/${id}/materials`)); }
        catch { if (current(id)) { el('taskReadingMessage').textContent='状态查询失败，请刷新后重试。'; } }
      }, 2000);
    }
  }
  function update(state) {
    if (state.source_hash !== materials?.source_hash) needsWorkspaceReload = true;
    materials = state; hooks?.onMaterials(state); render();
  }
  async function perform(operation) {
    const id = currentId(); if (!id || busy) return;
    busy = true; render();
    try { await operation(id); } catch(error) { if (current(id)) notify(error.message); }
    finally {busy = false; if (current(id)) render();}
  }
  async function saveText(id) {
    if (!current(id)) throw new Error('当前精准教学已切换，请在原项目继续');
    await saveIdeas();
    if (!current(id)) throw new Error('当前精准教学已切换，请在原项目继续');
    await hooks.saveDraft();
    if (!current(id)) throw new Error('当前精准教学已切换，请在原项目中继续');
    update(await PlatformAPI.request(`/api/precision-teachings/${id}/materials`)); changed = false; dirty = false;
  }
  async function upload(files) {
    if (readingDirty) { notify('请先保存正在修改的附件识别内容'); return; }
    const selected=[...files];if (!selected.length) return;
    const isImage=file=>['image/png','image/jpeg','image/webp'].includes(file.type);
    if (selected.some(file=>!isImage(file) && !/\.(pdf|docx|txt)$/i.test(file.name))) {notify('支持图片、PDF、Word（.docx）和TXT');return;}
    if (selected.some(file=>file.size > 5*1024*1024)) {notify('每份附件最多5MB'); return;}
    if (selected.filter(isImage).length + (materials?.images?.length || 0) > 4) {notify('每个任务最多保留4张图片'); return;}
    await perform(async id => {
      await saveText(id);
      for (const file of selected) {
        if (!current(id)) return;
        update(await PlatformAPI.request(`/api/precision-teachings/${id}/materials/${isImage(file) ? 'images' : 'files'}`, {method:'POST',headers:{'Content-Type':file.type || 'application/octet-stream','X-File-Name':encodeURIComponent(file.name),'X-Material-Version':materials.source_hash,'X-Defer-Recognition':'1'},body:file}));
      }
      if (!current(id)) return;
      if (selected.some(isImage)) update(await PlatformAPI.request(`/api/precision-teachings/${id}/materials/recognition/start`,{method:'POST'}));
      await hooks.reload(id);
    });
  }
  el('taskImageUploadButton').addEventListener('click',()=>{el('taskAttachmentActions').hidden=!el('taskAttachmentActions').hidden;});
  el('taskUploadFileChoice').addEventListener('click',()=>{el('taskAttachmentActions').hidden=true;el('taskImageInput').click();});
  el('taskAddWebLink').addEventListener('click',()=>{el('taskAttachmentActions').hidden=true;el('taskWebLinkForm').reset();el('taskWebLinkDialog').showModal();});
  el('taskWebLinkCancel').addEventListener('click',()=>el('taskWebLinkDialog').close());
  el('taskWebLinkForm').addEventListener('submit',async event=>{
    event.preventDefault();
    const draft={url:el('taskWebLinkUrl').value.trim(),name:el('taskWebLinkName').value.trim(),content_text:el('taskWebLinkContent').value.trim()};
    await perform(async id=>{
      await saveText(id);
      update(await PlatformAPI.request(`/api/precision-teachings/${id}/materials/links`,{method:'POST',body:JSON.stringify({...draft,source_hash:materials.source_hash})}));
      el('taskWebLinkDialog').close();await hooks.reload(id);
    });
  });
  el('taskImageInput').addEventListener('change',event=>{upload(event.target.files);event.target.value='';});
  editor.addEventListener('paste',event=>{
    const images=[...(event.clipboardData?.items || [])].filter(item=>item.kind==='file' && item.type.startsWith('image/')).map(item=>item.getAsFile()).filter(Boolean);
    event.preventDefault();
    if (images.length) upload(images);
    else {rememberSelection(); insertNode(document.createTextNode(event.clipboardData?.getData('text/plain') || '')); sync();}
  });
  editor.addEventListener('dragover',event=>{if ([...(event.dataTransfer?.types || [])].includes('Files')) event.preventDefault();});
  editor.addEventListener('drop',event=>{if (event.dataTransfer?.files.length) {event.preventDefault();upload(event.dataTransfer.files);}});
  for (const id of ['diagnosisDesignIntent', 'diagnosisRoleText', 'diagnosisDuration']) {
    el(id).addEventListener('input',()=>{dirty=true;});
  }
  document.querySelectorAll('input[name="diagnosisTiming"]').forEach(input => {
    input.addEventListener('change',()=>{dirty=true;});
  });
  el('taskGenerationIdeas').addEventListener('input',()=>{
    el('taskGenerationSaveState').textContent = el('taskGenerationIdeas').value.trim() === savedIdeas ? '' : '生成或保存草稿时保存';
    document.dispatchEvent(new CustomEvent('task-generation-ideas-changed'));
  });
  async function saveIdeas() {
    const id = currentId(), value = el('taskGenerationIdeas').value.trim();
    if (value === savedIdeas) return;
    const response = await PlatformAPI.saveDiagnosticTaskPreferences(id, value);
    if (!current(id)) throw new Error('当前精准教学已切换，请在原项目继续');
    savedIdeas = response.teacher_ideas;
    hooks?.onPreferences?.(response);
    el('taskGenerationSaveState').textContent = el('taskGenerationIdeas').value.trim() === savedIdeas ? '已保存' : '仍有修改待保存';
  }
  el('taskReadingText').addEventListener('input',()=>{readingDirty=true;renderReading();});
  el('retryTaskReading').addEventListener('click',()=>{
    if (readingDirty) {notify('请先保存已修改的附件内容，重新识别会生成新草稿');return;}
    perform(async id=>{await saveText(id);update(await PlatformAPI.request(`/api/precision-teachings/${id}/materials/recognition/start`,{method:'POST'}));});
  });
  el('saveTaskReading').addEventListener('click',()=>{
    if (!materials?.images?.length) return;
    const draftText=el('taskReadingText').value, imageHash=readingImageHash;
    if (!draftText.trim()) {notify('附件识别内容尚为空，请先识别图片，或在“查看识别内容”中补充后保存');return;}
    perform(async id=>{
      // Save teacher task/intent before reloading the workspace; never discard an unsaved explanation.
      await saveText(id);
      if (materials.image_hash !== imageHash) throw new Error('附件已变化，请刷新后重新核对，未覆盖新附件');
      const state=await PlatformAPI.request(`/api/precision-teachings/${id}/materials/recognition`,{method:'PUT',body:JSON.stringify({source_hash:materials.source_hash,task_text:draftText,diagram_notes:''})});
      readingDirty=false;update(state);await hooks.reload(id);
    });
  });
  window.TaskInput = {
    configure(value) {hooks=value;},
    applyWorkspace(workspace) {
      if (workspaceId !== currentId()) {workspaceId=currentId();readingDirty=false;readingKey='';clearTimeout(polling);polling=null;el('taskReadingDetails').open=false;el('regenerateTasks').disabled=false;}
      materials=workspace.task_materials || null;changed=false;dirty=false;needsWorkspaceReload=false;loadEditor();render();
      savedIdeas = workspace.task_design_preferences?.teacher_ideas || '';
      el('taskGenerationIdeas').value = savedIdeas;
      el('taskGenerationIdeas').disabled = false;
      el('taskGenerationSaveState').textContent = '';
    },
    requiresConfirmation,canonicalText,
    refreshPreview() {dirty=true;loadEditor();render();},
    async prepareAnalysis() {
      if (materials?.images?.length && readingDirty) throw new Error('附件内容尚未保存，请先核对并保存识别内容');
      await saveIdeas();
      if (dirty) await this.saveDraft();
      else if (needsWorkspaceReload) {
        // A completed polling job invalidates the old rubric on the server.
        await hooks.reload(currentId());
      }
    },
    async saveDraft() {
      if (busy) throw new Error('图片正在处理，请稍后保存');
      const id=currentId();busy=true;
      try {await saveText(id);await hooks.reload(id);} finally {busy=false;render();}
    },
    saveGenerationIdeas: saveIdeas,
    renderPublish() {TaskMath.render(el('publishTaskPreviewText'),canonicalText(false) || '尚未填写任务内容，请返回诊断任务步骤补充。');renderImages(el('publishTaskImages'),false);renderAttachments(el('publishTaskFiles'),false);},
  };
  // Retain the legacy field only as the existing API's plain-text/LaTeX storage.
  backing.hidden = true;
  document.querySelector('label[for="diagnosisTaskText"]').htmlFor = 'diagnosisTaskEditor';
})();
