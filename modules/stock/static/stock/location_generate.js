// Location generator: numbers the level rows for the form and previews what
// will be made. The labels follow inventory/locations.py exactly.
(function () {
  const form = document.getElementById('gen-form');
  const rows = document.getElementById('gen-rows');
  const parentSelect = document.getElementById('gen-parent');
  const parentCodes = JSON.parse(document.getElementById('gen-parent-codes').textContent);
  const MAX = window.GEN_MAX || 5000;

  function lettersToIndex(text) {
    let n = 0;
    for (const ch of text.toUpperCase()) {
      if (ch < 'A' || ch > 'Z') return null;
      n = n * 26 + (ch.charCodeAt(0) - 64);
    }
    return n ? n - 1 : null;
  }
  function indexToLetters(i) {
    let out = '';
    i += 1;
    while (i) { const rem = (i - 1) % 26; out = String.fromCharCode(65 + rem) + out; i = Math.floor((i - 1) / 26); }
    return out;
  }
  // The label at position i (0-based) of a level; null if "start at" doesn't fit the style.
  function labelAt(style, start, count, i) {
    start = (start || '').trim();
    if (style === 'A' || style === 'a') {
      const first = start ? lettersToIndex(start) : 0;
      if (first === null) return null;
      const l = indexToLetters(first + i);
      return style === 'a' ? l.toLowerCase() : l;
    }
    if (start && !/^-?\d+$/.test(start)) return null;
    const first = start ? parseInt(start, 10) : 1;
    const n = first + i;
    if (style === '01') return String(n).padStart(Math.max(2, String(first + count - 1).length), '0');
    return String(n);
  }

  function levels() {
    return [...rows.querySelectorAll('tr')].map(tr => {
      const get = key => tr.querySelector(`[data-key=${key}]`).value;
      return { name: get('name').trim(), count: parseInt(get('count'), 10), style: get('style'), start: get('start') };
    }).filter(l => l.name || !isNaN(l.count));
  }

  function renumber() {
    [...rows.querySelectorAll('tr')].forEach((tr, i) => {
      tr.querySelector('.level-no').textContent = i + 1;
      tr.querySelectorAll('[data-key]').forEach(el => { el.name = `level-${i}-${el.dataset.key}`; });
      tr.querySelector('.remove').hidden = rows.children.length < 2;
    });
  }

  function preview() {
    const summary = document.getElementById('gen-summary');
    const example = document.getElementById('gen-example');
    const codes = document.getElementById('gen-codes');
    const submit = document.getElementById('gen-submit');
    const ls = levels();
    const problems = [];
    let total = 0, product = 1;
    ls.forEach((l, i) => {
      if (!l.name) problems.push(`Level ${i + 1} needs a name.`);
      if (isNaN(l.count) || l.count < 1 || l.count > 500) { problems.push(`Level ${i + 1}: how many must be 1 to 500.`); return; }
      if (labelAt(l.style, l.start, l.count, 0) === null) problems.push(`Level ${i + 1}: "start at" doesn't match its labels.`);
      product *= l.count;
      total += product;
    });
    if (!ls.length) problems.push('Add at least one level.');
    if (total > MAX) problems.push(`That's ${total} locations; make at most ${MAX} at once.`);
    submit.disabled = problems.length > 0;
    if (problems.length) {
      summary.innerHTML = '';
      summary.append(...problems.map(p => Object.assign(document.createElement('div'), { className: 'error-text', textContent: p })));
      example.textContent = '';
      codes.textContent = '';
      return;
    }
    // e.g. "4 rack rows × 6 racks × 4 shelves = 96 shelves (124 locations in all)"
    const leafCount = product;
    summary.textContent = ls.map(l => `${l.count} × ${l.name}`).join(' → ') +
      ` = ${leafCount} ${ls[ls.length - 1].name.toLowerCase()} location${leafCount === 1 ? '' : 's'}` +
      (total !== leafCount ? ` (${total} locations in all)` : '');
    const prefix = parentCodes[parentSelect.value] || '';
    const parentName = parentSelect.value ? parentSelect.selectedOptions[0].textContent.replace(/ \([^)]*\)$/, '') + ' > ' : '';
    example.textContent = 'e.g. ' + parentName + ls.map(l => `${l.name} ${labelAt(l.style, l.start, l.count, 0)}`).join(' > ');
    // First and last few codes of the deepest level.
    const codeAt = index => {
      let out = '', rest = index;
      const parts = [];
      for (let d = ls.length - 1; d >= 0; d--) {
        parts.unshift(labelAt(ls[d].style, ls[d].start, ls[d].count, rest % ls[d].count));
        rest = Math.floor(rest / ls[d].count);
      }
      out = prefix + parts.join('');
      return out;
    };
    const shown = [];
    const head = Math.min(leafCount, 6);
    for (let i = 0; i < head; i++) shown.push(codeAt(i));
    if (leafCount > 8) shown.push('…');
    for (let i = Math.max(head, leafCount - 2); i < leafCount; i++) shown.push(codeAt(i));
    codes.innerHTML = '';
    codes.append(...shown.map(c => Object.assign(document.createElement(c === '…' ? 'span' : 'code'), { textContent: c })));
  }

  function addRow() {
    const last = rows.lastElementChild;
    const tr = last.cloneNode(true);
    tr.querySelectorAll('input').forEach(i => { i.value = ''; });
    rows.append(tr);
    renumber();
    preview();
    tr.querySelector('[data-key=name]').focus();
  }

  rows.addEventListener('input', preview);
  rows.addEventListener('change', preview);
  rows.addEventListener('click', e => {
    if (!e.target.classList.contains('remove') || rows.children.length < 2) return;
    e.target.closest('tr').remove();
    renumber();
    preview();
  });
  parentSelect.addEventListener('change', preview);
  document.getElementById('gen-add').addEventListener('click', addRow);
  form.addEventListener('submit', renumber);

  renumber();
  preview();
})();
