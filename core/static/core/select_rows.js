// Selectable rows: click a row (or its checkbox) to select it; the bar
// ([data-select-bar]) shows how many distinct records are selected and
// enables its button. Rows of the same record (e.g. several moves of one
// part) are selected together.
(function () {
  const bar = document.querySelector('[data-select-bar]');
  const tables = [...document.querySelectorAll('table[data-select]')];
  if (!bar || !tables.length) return;
  const boxes = () => tables.flatMap(t => [...t.querySelectorAll('input.row-select')]);
  const count = bar.querySelector('[data-select-count]');
  const submit = bar.querySelector('[data-select-submit]');
  const clear = bar.querySelector('[data-select-clear]');
  const idle = count.textContent;

  function update() {
    const chosen = new Set(boxes().filter(b => b.checked).map(b => b.value));
    boxes().forEach(b => b.closest('tr').classList.toggle('selected', b.checked));
    const noun = bar.dataset.noun || "part";
    count.textContent = chosen.size ? `${chosen.size} ${noun}${chosen.size === 1 ? "" : "s"} selected` : idle;
    submit.disabled = !chosen.size;
    if (clear) clear.hidden = !chosen.size;
    tables.forEach(t => {
      const all = t.querySelector('[data-select-all]');
      const mine = [...t.querySelectorAll('input.row-select')];
      if (all) {
        all.checked = mine.length > 0 && mine.every(b => b.checked);
        all.indeterminate = !all.checked && mine.some(b => b.checked);
      }
    });
  }

  function set(value, checked) {
    boxes().filter(b => b.value === value).forEach(b => { b.checked = checked; });
  }

  tables.forEach(t => {
    t.addEventListener('change', e => {
      if (e.target.matches('input.row-select')) { set(e.target.value, e.target.checked); update(); }
      if (e.target.matches('[data-select-all]')) {
        t.querySelectorAll('input.row-select').forEach(b => set(b.value, e.target.checked));
        update();
      }
    });
    // A click anywhere on a row selects it, except on its links and controls.
    t.addEventListener('click', e => {
      if (e.target.closest('a, button, input, select, textarea, label')) return;
      const box = e.target.closest('tr') && e.target.closest('tr').querySelector('input.row-select');
      if (!box) return;
      set(box.value, !box.checked);
      update();
    });
  });
  if (clear) clear.addEventListener('click', () => { boxes().forEach(b => { b.checked = false; }); update(); });
  update();
})();
