// Part picker: search parts, "Add" one (fires "part-picked" on the picker), or
// make one on the fly with "Quick part" (part number + description).
(function () {
  if (window.PartPicker) { window.PartPicker.init(); return; }

  function csrf() {
    const input = document.querySelector('input[name=csrfmiddlewaretoken]');
    if (input) return input.value;
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function setup(el) {
    if (el.dataset.ready) return;
    el.dataset.ready = '1';
    const search = el.querySelector('.picker-search');
    const body = el.querySelector('.picker-results tbody');
    const quick = el.querySelector('.picker-quick');
    const error = el.querySelector('.picker-error');
    const addLabel = el.dataset.addLabel || 'Add';
    const exclude = new Set(String(el.dataset.exclude || '').split(',').filter(Boolean));
    let timer = null, seq = 0;

    const pick = part => el.dispatchEvent(new CustomEvent('part-picked', { detail: part, bubbles: true }));

    async function load() {
      const mine = ++seq;
      const res = await fetch(`${el.dataset.searchUrl}?q=${encodeURIComponent(search.value.trim())}`, { credentials: 'same-origin' });
      if (!res.ok || mine !== seq) return;
      const { parts } = await res.json();
      const shown = parts.filter(p => !exclude.has(String(p.id)));
      body.innerHTML = shown.length ? shown.map(p => `
        <tr data-id="${p.id}">
          <td><a href="${esc(p.url)}" target="_blank">${esc(p.part_number)}</a></td>
          <td>${esc(p.name)}</td>
          <td class="muted">${esc(p.category)}</td>
          <td><button type="button" class="btn small picker-add">${esc(addLabel)}</button></td>
        </tr>`).join('')
        : `<tr><td colspan="4" class="muted">No parts found${search.value.trim() ? ` for "${esc(search.value.trim())}"` : ''}. Use "+ Quick part" to add one.</td></tr>`;
      body.querySelectorAll('tr[data-id]').forEach(tr => {
        const part = shown.find(p => String(p.id) === tr.dataset.id);
        tr.querySelector('.picker-add').addEventListener('click', () => pick(part));
      });
    }

    search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(load, 200); });
    search.addEventListener('keydown', e => {
      if (e.key !== 'Enter') return;
      e.preventDefault();  // never submit the surrounding form from the search box
      const first = body.querySelector('.picker-add');
      if (first) first.click();
    });

    el.querySelector('.picker-quick-toggle').addEventListener('click', () => {
      quick.hidden = !quick.hidden;
      if (!quick.hidden) {
        const number = quick.querySelector('.picker-quick-number');
        if (!number.value) number.value = search.value.trim();
        number.focus();
      }
    });
    quick.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); quick.querySelector('.picker-quick-add').click(); } });
    quick.querySelector('.picker-quick-add').addEventListener('click', async () => {
      const part_number = quick.querySelector('.picker-quick-number').value.trim();
      const name = quick.querySelector('.picker-quick-name').value.trim();
      error.textContent = '';
      if (!part_number) { error.textContent = 'Enter a part number.'; return; }
      const res = await fetch(el.dataset.quickUrl, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
        body: JSON.stringify({ part_number, name }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        error.textContent = Object.values(data.errors || {}).flat().join(' ') || 'Could not create the part.';
        return;
      }
      quick.querySelector('.picker-quick-number').value = '';
      quick.querySelector('.picker-quick-name').value = '';
      quick.hidden = true;
      pick(data.part);
      load();
    });

    load();
  }

  window.PartPicker = { init: () => document.querySelectorAll('[data-part-picker]').forEach(setup) };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', window.PartPicker.init);
  else window.PartPicker.init();
})();
