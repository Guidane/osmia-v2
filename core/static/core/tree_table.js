// Tree tables: rows in tree order with data-id / data-parent / data-depth.
// ▸/▾ opens and closes a row's children, what's open is remembered per
// table, and the filter box shows matching rows with the rows above them.
(function () {
  document.querySelectorAll('table[data-tree]').forEach(table => {
    const key = 'osmia-tree-open:' + table.dataset.tree;
    const rows = [...table.querySelectorAll('tbody tr[data-id]')];
    if (!rows.length) return;
    const byId = new Map(rows.map(r => [r.dataset.id, r]));
    const kids = new Map();
    for (const r of rows) {
      const p = r.dataset.parent;
      if (p && byId.has(p)) (kids.get(p) || kids.set(p, []).get(p)).push(r);
    }

    let open;
    try { open = new Set(JSON.parse(localStorage.getItem(key))); } catch (e) { open = null; }
    if (!open || !(localStorage.getItem(key))) {
      // First visit: show the top level and what's directly inside it.
      open = new Set(rows.filter(r => r.dataset.depth === '0').map(r => r.dataset.id));
    }
    const save = () => { try { localStorage.setItem(key, JSON.stringify([...open])); } catch (e) { /* private mode */ } };

    const filterBox = document.querySelector(`[data-tree-filter="${table.dataset.tree}"]`);
    let filter = '';

    function visibleByTree(r) {
      for (let p = r.dataset.parent; p && byId.has(p); p = byId.get(p).dataset.parent) {
        if (!open.has(p)) return false;
      }
      return true;
    }

    function render() {
      let matches = null;
      if (filter) {
        // A row shows if it matches, or something inside it does.
        matches = new Set();
        for (const r of rows) {
          if (!r.textContent.toLowerCase().includes(filter)) continue;
          for (let n = r; n; n = byId.get(n.dataset.parent)) matches.add(n);
        }
      }
      for (const r of rows) {
        const hasKids = kids.has(r.dataset.id);
        const toggle = r.querySelector('.tree-toggle');
        toggle.hidden = !hasKids;
        const expanded = filter ? true : open.has(r.dataset.id);
        toggle.textContent = expanded ? '▾' : '▸';
        toggle.setAttribute('aria-expanded', String(expanded));
        r.hidden = matches ? !matches.has(r) : !visibleByTree(r);
      }
    }

    table.addEventListener('click', e => {
      const toggle = e.target.closest('.tree-toggle');
      if (!toggle || filter) return;
      const id = toggle.closest('tr').dataset.id;
      open.has(id) ? open.delete(id) : open.add(id);
      save();
      render();
    });
    const expandAll = document.querySelector(`[data-tree-expand="${table.dataset.tree}"]`);
    const collapseAll = document.querySelector(`[data-tree-collapse="${table.dataset.tree}"]`);
    if (expandAll) expandAll.addEventListener('click', () => { open = new Set(kids.keys()); save(); render(); });
    if (collapseAll) collapseAll.addEventListener('click', () => { open = new Set(); save(); render(); });
    if (filterBox) filterBox.addEventListener('input', () => { filter = filterBox.value.trim().toLowerCase(); render(); });

    render();
  });
})();
