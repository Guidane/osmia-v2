// Receive from a table: paste "Part number,Quantity,Location" rows (e.g. what an
// AI read off a delivery note) and tick the matching order lines.
// parseTable() and matchRows() are plain functions, so they can be tested with node.
(function (root) {
  const HEADERS = { part: ['part number', 'part', 'part no', 'part no.', 'pn'], qty: ['quantity', 'qty'], location: ['location', 'loc'] };

  function splitLine(line, sep) {
    const cells = [];
    let cell = '', quoted = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (quoted) {
        if (c === '"' && line[i + 1] === '"') { cell += '"'; i++; }
        else if (c === '"') quoted = false;
        else cell += c;
      } else if (c === '"') quoted = true;
      else if (c === sep) { cells.push(cell.trim()); cell = ''; }
      else cell += c;
    }
    cells.push(cell.trim());
    return cells;
  }

  // Rows as {part, qty (number or null), location, line (1-based)}; errors for rows that can't be read.
  function parseTable(text) {
    const lines = text.split(/\r?\n/).map(l => l.trim()).filter(l => l && !l.startsWith('```'));
    const rows = [], errors = [];
    if (!lines.length) return { rows, errors };
    const sep = lines[0].includes('\t') ? '\t' : (lines[0].includes(';') && !lines[0].includes(',') ? ';' : ',');
    let cols = { part: 0, qty: 1, location: 2 };
    const first = splitLine(lines[0], sep).map(h => h.toLowerCase());
    const isHeader = first.some(h => Object.values(HEADERS).some(names => names.includes(h)));
    if (isHeader) {
      for (const [key, names] of Object.entries(HEADERS)) cols[key] = first.findIndex(h => names.includes(h));
      if (cols.part < 0) errors.push('The header has no "Part number" column.');
    }
    if (errors.length) return { rows, errors };
    lines.slice(isHeader ? 1 : 0).forEach((line, i) => {
      const n = i + (isHeader ? 2 : 1);
      const cells = splitLine(line, sep);
      const part = cells[cols.part] || '';
      const rawQty = cols.qty >= 0 ? (cells[cols.qty] || '').replace(/\s/g, '') : '';
      const location = cols.location >= 0 ? (cells[cols.location] || '') : '';
      if (!part) { errors.push(`Line ${n}: no part number.`); return; }
      let qty = null;
      if (rawQty) {
        qty = Number(rawQty.replace(',', '.'));
        if (!Number.isFinite(qty) || qty < 0) { errors.push(`Line ${n}: "${cells[cols.qty]}" is not a quantity.`); return; }
      }
      rows.push({ part, qty, location, line: n });
    });
    return { rows, errors };
  }

  const norm = s => String(s || '').trim().toLowerCase();

  // lines: [{id, part, qty}]; locations: [{id, code, path}].
  // Returns {tick: {lineId: locationId or ''}, notes: [{kind: ok|warn|error, text}]}.
  function matchRows(rows, lines, locations) {
    const tick = {}, notes = [];
    const used = new Set();
    for (const row of rows) {
      const line = lines.find(l => norm(l.part) === norm(row.part) && !used.has(l.id));
      if (!line) {
        const again = lines.some(l => norm(l.part) === norm(row.part));
        notes.push({ kind: 'error', text: `${row.part}: ${again ? 'listed more often than it is on the order' : 'not an open line of this order'}.` });
        continue;
      }
      let location = '';
      if (row.location) {
        const loc = locations.find(l => (l.code && norm(l.code) === norm(row.location)) || norm(l.path) === norm(row.location));
        if (loc) location = loc.id;
        else notes.push({ kind: 'warn', text: `${row.part}: no location "${row.location}"; pick one.` });
      }
      if (row.qty !== null && row.qty < line.qty) {
        notes.push({ kind: 'warn', text: `${row.part}: ${row.qty} came of ${line.qty} ordered. Left unticked, since a line is received whole; tick it if that's all that's coming.` });
        used.add(line.id);
        continue;
      }
      used.add(line.id);
      tick[line.id] = location;
      notes.push(row.qty !== null && row.qty > line.qty
        ? { kind: 'warn', text: `${row.part}: ${row.qty} came, ${line.qty} ordered. Ticked; only ${line.qty} are booked.` }
        : { kind: 'ok', text: `${row.part}: ticked.` });
    }
    for (const l of lines) {
      if (!used.has(l.id)) notes.push({ kind: 'warn', text: `${l.part}: not in the table, left unticked.` });
    }
    return { tick, notes };
  }

  root.ReceiveImport = { parseTable, matchRows };
  if (typeof module !== 'undefined') module.exports = root.ReceiveImport;

  if (typeof document === 'undefined') return;
  document.addEventListener('DOMContentLoaded', () => {
    const box = document.getElementById('receive-import');
    if (!box) return;
    const form = document.getElementById('receive-form');
    const out = box.querySelector('.import-notes');
    const lineRows = [...form.querySelectorAll('tr[data-line]')];
    const lines = lineRows.map(tr => ({ id: tr.dataset.line, part: tr.dataset.part, qty: Number(tr.dataset.qty) }));
    const locations = [...form.querySelector('select.line-location').options].filter(o => o.value)
      .map(o => ({ id: o.value, code: o.dataset.code, path: o.dataset.path }));

    function show(notes) {
      out.innerHTML = '';
      for (const n of notes) {
        const li = document.createElement('li');
        li.className = 'import-' + n.kind;
        li.textContent = (n.kind === 'ok' ? '✓ ' : n.kind === 'warn' ? '⚠ ' : '✕ ') + n.text;
        out.appendChild(li);
      }
    }

    box.querySelector('.btn-read').addEventListener('click', () => {
      const { rows, errors } = parseTable(box.querySelector('textarea').value);
      if (errors.length || !rows.length) {
        show((errors.length ? errors : ['Paste a table first.']).map(text => ({ kind: 'error', text })));
        return;
      }
      const { tick, notes } = matchRows(rows, lines, locations);
      for (const tr of lineRows) {
        const ticked = tr.dataset.line in tick;
        tr.querySelector('input[type=checkbox]').checked = ticked;
        if (ticked && tick[tr.dataset.line]) tr.querySelector('select.line-location').value = tick[tr.dataset.line];
      }
      show(notes);
    });

    box.querySelector('.btn-copy').addEventListener('click', async e => {
      const text = document.getElementById('ai-instructions').value.trim();
      try {
        await navigator.clipboard.writeText(text);
      } catch (err) {  // not a secure context: fall back to a hidden textarea
        const t = document.createElement('textarea');
        t.value = text;
        document.body.appendChild(t);
        t.select();
        document.execCommand('copy');
        t.remove();
      }
      const btn = e.currentTarget, label = btn.textContent;
      btn.textContent = 'Copied ✓';
      setTimeout(() => { btn.textContent = label; }, 1500);
    });
  });
})(typeof window !== 'undefined' ? window : globalThis);
