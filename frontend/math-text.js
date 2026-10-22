/* Shared, text-safe LaTeX rendering for teacher and student task materials. */
(() => {
  if (window.MathfieldElement) {
    MathfieldElement.fontsDirectory = '/vendor/mathlive/fonts';
    MathfieldElement.soundsDirectory = null;
  }
  const formulaPattern = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|(?<!\\)\$([^$\n]+?)\$/g;
  function render(element, value, onEdit = null) {
    const text = String(value || '');
    element.replaceChildren();
    element.classList.add('math-text');
    let cursor = 0;
    for (const match of text.matchAll(formulaPattern)) {
      element.append(document.createTextNode(text.slice(cursor, match.index)));
      const latex = match[1] || match[2] || match[3] || match[4];
      const wrapper = document.createElement('span');
      wrapper.className = match[1] || match[2] ? 'math-expression math-display' : 'math-expression';
      if (customElements.get('math-field')) {
        const formula = document.createElement('math-field');
        formula.readOnly = true;
        formula.setAttribute('math-virtual-keyboard-policy', 'manual');
        formula.setAttribute('tabindex', '-1');
        formula.value = latex;
        wrapper.append(formula);
      } else wrapper.textContent = match[0];
      if (onEdit) {
        wrapper.classList.add('math-editable');
        wrapper.tabIndex = 0;
        wrapper.setAttribute('role', 'button');
        wrapper.setAttribute('aria-label', '编辑公式');
        const edit = () => onEdit(latex, match.index, match.index + match[0].length);
        wrapper.addEventListener('click', edit);
        wrapper.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); edit(); } });
      }
      element.append(wrapper);
      cursor = match.index + match[0].length;
    }
    element.append(document.createTextNode(text.slice(cursor)));
  }
  function typeset(element) {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.parentElement.closest('math-field, .math-expression, textarea, code, pre, [contenteditable="true"]') && /\\[([]|\$/.test(node.textContent)) nodes.push(node);
    }
    for (const node of nodes) {
      const span = document.createElement('span');
      render(span, node.textContent);
      node.replaceWith(span);
    }
  }
  window.TaskMath = { render, typeset };
})();
