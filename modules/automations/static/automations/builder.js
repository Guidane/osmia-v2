// Rule builder: edits the rule's conditions and steps, and writes them as JSON
// into the form's hidden "conditions" and "actions" fields on submit.
(function () {
  const cat = JSON.parse(document.getElementById('rule-catalogue').textContent);
  const form = document.getElementById('rule-form');
  const trigger = form.elements.trigger;
  const conditionsInput = form.elements.conditions;
  const actionsInput = form.elements.actions;
  const conditionsBox = document.getElementById('conditions');
  const stepsBox = document.getElementById('steps');

  const events = Object.fromEntries(cat.events.map(e => [e.key, e]));
  const actions = Object.fromEntries(cat.actions.map(a => [a.key, a]));
  const opt = cat.options;

  function parse(input) {
    try { const v = JSON.parse(input.value || '[]'); return Array.isArray(v) ? v : []; } catch (e) { return []; }
  }
  const state = { conditions: parse(conditionsInput), steps: parse(actionsInput) };

  // -- tiny DOM helpers -------------------------------------------------------------
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'class') node.className = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) if (c !== null && c !== undefined) node.append(c);
    return node;
  }
  function select(choices, value, onchange, blank) {
    const s = el('select', { onchange: e => onchange(e.target.value) });
    if (blank !== undefined) s.append(el('option', { value: '' }, blank));
    for (const [v, label] of choices) s.append(el('option', { value: v }, label));
    s.value = value === undefined || value === null ? '' : String(value);
    return s;
  }
  function groupedActionSelect(value, onchange) {
    const s = el('select', { onchange: e => onchange(e.target.value) }, el('option', { value: '' }, 'Pick what to do…'));
    const groups = {};
    for (const a of cat.actions) (groups[a.module] = groups[a.module] || []).push(a);
    for (const [module, list] of Object.entries(groups)) {
      s.append(el('optgroup', { label: module }, list.map(a => el('option', { value: a.key }, a.label))));
    }
    s.value = value || '';
    return s;
  }
  function removeButton(onclick, title) {
    return el('button', { type: 'button', class: 'btn small remove', title, onclick }, '✕');
  }

  // -- If: conditions ------------------------------------------------------------------
  function currentFields() {
    const e = events[trigger.value];
    return e ? e.fields : [];
  }

  function renderConditions() {
    conditionsBox.replaceChildren();
    const fields = currentFields();
    document.getElementById('btn-add-condition').disabled = !fields.length;
    if (!fields.length) {
      conditionsBox.append(el('p', { class: 'muted' }, trigger.value ? 'This trigger has nothing to test.' : 'Pick a trigger first.'));
      return;
    }
    state.conditions.forEach((c, i) => {
      const field = fields.find(f => f.key === c.field);
      const kind = field ? field.kind : 'text';
      const ops = (cat.opsByKind[kind] || cat.opsByKind.text).map(o => [o, cat.ops[o]]);
      if (!ops.some(([o]) => o === c.op)) c.op = ops[0][0];
      let value;
      if (kind === 'rule') {
        value = select(opt.rules, c.value, v => { c.value = v; }, 'Pick a rule…');
      } else {
        value = el('input', { type: kind === 'number' ? 'number' : 'text', step: 'any', value: c.value || '', oninput: e => { c.value = e.target.value; } });
      }
      conditionsBox.append(el('div', { class: 'condition' },
        i ? el('span', { class: 'muted and' }, 'and') : null,
        select(fields.map(f => [f.key, f.label]), c.field, v => { c.field = v; c.value = ''; renderConditions(); }),
        select(ops, c.op, v => { c.op = v; }),
        value,
        removeButton(() => { state.conditions.splice(i, 1); renderConditions(); }, 'Remove condition'),
      ));
    });
  }

  // -- Then: steps ---------------------------------------------------------------------
  function paramWidget(p, params) {
    const v = params[p.name];
    const set = x => { params[p.name] = x; };
    switch (p.type) {
      case 'textarea':
        return el('textarea', { rows: 2, oninput: e => set(e.target.value) }, v || '');
      case 'number':
        return el('input', { type: 'number', step: 'any', value: v ?? '', oninput: e => set(e.target.value) });
      case 'bool':
        return el('input', { type: 'checkbox', checked: !!v, onchange: e => set(e.target.checked) });
      case 'select':
        return select(p.choices, v, set, p.required ? 'Pick…' : '—');
      case 'status':
        return select(opt.statuses, v, set, 'Pick a status…');
      case 'target':
        if (v === undefined) set('object');
        return select(opt.targets, params[p.name], set);
      case 'user':
        return select(opt.users, v, x => set(x ? Number(x) : null), '—');
      case 'department':
        return select(opt.departments, v, x => set(x ? Number(x) : null), '—');
      case 'part':
        return select(opt.parts, v, x => set(x ? Number(x) : null), 'Pick a part…');
      case 'users': {
        const chosen = new Set((v || []).map(String));
        return el('div', { class: 'people' }, opt.users.map(([id, name]) => el('label', {},
          el('input', {
            type: 'checkbox', checked: chosen.has(String(id)),
            onchange: e => {
              e.target.checked ? chosen.add(String(id)) : chosen.delete(String(id));
              set([...chosen].map(Number));
            },
          }), ' ', name)));
      }
      case 'task_list':
        return taskList(p, params);
      default:
        return el('input', { type: 'text', value: v || '', oninput: e => set(e.target.value) });
    }
  }

  function taskList(p, params) {
    if (!Array.isArray(params[p.name]) || !params[p.name].length) params[p.name] = [{ title: '' }];
    const rows = params[p.name];
    const people = [['@owner', "The record's owner"], ...opt.users];
    const wrap = el('div', { class: 'task-list' });
    function draw() {
      wrap.replaceChildren(el('table', { class: 'list compact' },
        el('thead', {}, el('tr', {}, ['Task', 'Assign to', 'Department', 'Priority', 'Due in days', ''].map(h => el('th', {}, h)))),
        el('tbody', {}, rows.map((t, i) => el('tr', {},
          el('td', {},
            el('input', { type: 'text', placeholder: 'Title, e.g. Check {source} on arrival', value: t.title || '', oninput: e => { t.title = e.target.value; } }),
            el('input', { type: 'text', class: 'details', placeholder: 'Details (optional)', value: t.description || '', oninput: e => { t.description = e.target.value; } })),
          el('td', {}, select(people, t.assignee, x => { t.assignee = x; }, 'Nobody')),
          el('td', {}, select(opt.departments, t.department, x => { t.department = x; }, "Assignee's")),
          el('td', {}, select(p.choices, t.priority, x => { t.priority = x; }, 'Normal')),
          el('td', {}, el('input', { type: 'number', step: 1, class: 'days', value: t.due_in_days ?? '', oninput: e => { t.due_in_days = e.target.value; } })),
          el('td', {}, rows.length > 1 ? removeButton(() => { rows.splice(i, 1); draw(); }, 'Remove task') : null),
        )))),
        el('button', { type: 'button', class: 'btn small', onclick: () => { rows.push({ title: '' }); draw(); } }, '+ Add task'));
    }
    draw();
    return wrap;
  }

  function renderSteps() {
    stepsBox.replaceChildren();
    state.steps.forEach((step, i) => {
      step.params = step.params || {};
      const action = actions[step.action];
      const body = el('div', { class: 'params' });
      if (action) {
        for (const p of action.params) {
          body.append(el('div', { class: 'param param-' + p.type },
            el('label', {}, p.label, p.required ? el('span', { class: 'req' }, ' *') : null),
            paramWidget(p, step.params),
            p.help ? el('small', { class: 'muted' }, p.help) : null));
        }
      } else if (step.action) {
        body.append(el('p', { class: 'flash error' }, `"${step.action}" is no longer available; pick another action or remove this step.`));
      }
      stepsBox.append(el('div', { class: 'step' },
        el('div', { class: 'step-head' },
          el('span', { class: 'step-no' }, String(i + 1)),
          groupedActionSelect(step.action, v => { step.action = v; step.params = {}; renderSteps(); }),
          el('div', { class: 'spacer' }),
          i ? el('button', { type: 'button', class: 'btn small', title: 'Move up', onclick: () => { state.steps.splice(i - 1, 0, ...state.steps.splice(i, 1)); renderSteps(); } }, '↑') : null,
          removeButton(() => { state.steps.splice(i, 1); renderSteps(); }, 'Remove step')),
        body));
    });
    if (!state.steps.length) stepsBox.append(el('p', { class: 'muted' }, 'No steps yet.'));
  }

  // -- wiring ------------------------------------------------------------------------
  document.getElementById('btn-add-condition').addEventListener('click', () => {
    const fields = currentFields();
    state.conditions.push({ field: fields.length ? fields[0].key : '', op: 'is', value: '' });
    renderConditions();
  });
  document.getElementById('btn-add-step').addEventListener('click', () => {
    state.steps.push({ action: '', params: {} });
    renderSteps();
  });
  trigger.addEventListener('change', () => {
    const keys = new Set(currentFields().map(f => f.key));
    state.conditions = state.conditions.filter(c => keys.has(c.field));
    renderConditions();
  });
  form.addEventListener('submit', () => {
    conditionsInput.value = JSON.stringify(state.conditions);
    actionsInput.value = JSON.stringify(state.steps);
  });

  if (!state.steps.length) state.steps.push({ action: '', params: {} });
  renderConditions();
  renderSteps();

  window.RuleBuilder = { state };  // for tests
})();
