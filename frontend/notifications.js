/* Only errors / blocked actions interrupt the user. Success stays inline.
 * Call show(message, detail, {kind:'error'|'blocked'}). Other kinds are silent.
 * No timer can remove an error; the user explicitly closes it.
 */
(() => {
  let serial = 0;
  const watchedDialogs = new WeakSet();
  function placeAboveDialogs(root) {
    const dialogs = [...document.querySelectorAll('dialog[open]')];
    const parent = dialogs.at(-1) || document.body;
    if (root.parentElement !== parent) {
      if (root.hidePopover && root.matches(':popover-open')) root.hidePopover();
      parent.append(root);
    }
    if (parent.tagName === 'DIALOG' && !watchedDialogs.has(parent)) {
      watchedDialogs.add(parent);
      parent.addEventListener('close', () => {
        if (root.parentElement !== parent) return;
        placeAboveDialogs(root);
        if (root.children.length && root.showPopover && !root.matches(':popover-open')) root.showPopover();
      });
    }
  }
  function explain(message, detail) {
    let title = String(message || '').trim();
    let explanation = String(detail || '').trim();
    if (/Failed to fetch|NetworkError|Load failed|fetch failed/i.test(title + explanation)) {
      title = title.replace(/Failed to fetch|NetworkError|Load failed|fetch failed/gi, '无法连接平台服务');
      explanation = '网络请求未能完成。请确认本地平台服务已启动、网络连接正常，再重试当前操作。';
    } else if (!explanation && /超时|timeout|timed out/i.test(title)) {
      explanation = '请求未在规定时间内完成。请稍后重试；若反复出现，请检查模型服务或网络连接。';
    } else if (!explanation && /失败|错误|异常|error|failed/i.test(title)) {
      const hasReason = /[：:]\s*\S/.test(title);
      explanation = (hasReason ? '' : '服务未返回更具体的错误原因。') +
        (/生成|模型|AI|量规/.test(title) ? '请根据上述原因检查输入和模型服务后重试。' : '请根据上述原因检查当前填写内容或服务连接后重试。');
    }
    return {title, detail:explanation};
  }
  function show(message, detail = '', options = {}) {
    if (!['error','blocked'].includes(options.kind)) return;
    const notice = explain(message, detail);
    if (!notice.title) return;
    message = notice.title; detail = notice.detail;
    const root = document.getElementById('toast');
    if (!root) return;
    placeAboveDialogs(root);
    root.hidden = false;
    const key = JSON.stringify([String(message),String(detail || '')]);
    const existing = [...root.children].find(item => item.dataset.noticeKey === key);
    if (existing) return existing.id;
    const error = options.kind === 'error' || /失败|错误|无法|不能|异常|超时|error|failed|timeout/i.test(String(message));
    const card = document.createElement('section');
    card.className = 'notice-card' + (error ? ' notice-error' : '');
    card.id = `notice-${++serial}`;
    card.dataset.noticeKey = key;
    card.setAttribute('role',error ? 'alert' : 'status');
    const content = document.createElement('div');
    const title = document.createElement('strong');title.textContent = String(message);
    content.append(title);
    if (detail) { const text = document.createElement('p');text.textContent = String(detail);content.append(text); }
    const close = document.createElement('button');close.type = 'button';close.className = 'notice-close';
    close.textContent = '×';close.setAttribute('aria-label','关闭提示');
    close.addEventListener('click',event => { event.stopPropagation(); card.remove(); if (!root.children.length) { if (root.hidePopover && root.matches(':popover-open')) root.hidePopover(); root.hidden = true; } });
    card.append(content,close);root.append(card);
    root.scrollTop = root.scrollHeight;
    if (root.showPopover && !root.matches(':popover-open')) root.showPopover();
    return card.id;
  }
  window.PlatformNotifications = {show};
})();
