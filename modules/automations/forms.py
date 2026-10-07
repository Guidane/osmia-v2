"""The rule builder: what it can offer (the catalogue) and checking what it sends back."""
import json
from decimal import Decimal, InvalidOperation

from django import forms
from django.apps import apps
from django.contrib.auth import get_user_model

from core import automation
from core.modules import get_module

from .engine import OPS
from .models import Rule

TARGETS = [
    ('object', 'The record that triggered the rule'),
    ('source', 'The record the chain started from (e.g. the order behind a group of tasks)'),
]
OPS_BY_KIND = {
    'text': ['is', 'is_not', 'contains'],
    'number': ['is', 'is_not', 'gt', 'lt'],
    'rule': ['is', 'is_not'],
}


def _module_title(label):
    module = get_module(label)
    return module.manifest.title if module else label.title()


def options(exclude_rule=None):
    """Choices for the builder's pickers: {kind: [[value, label], ...]}."""
    users = get_user_model().objects.filter(is_active=True).order_by('first_name', 'last_name', 'username')
    result = {
        'users': [[u.pk, u.get_full_name() or u.username] for u in users],
        'statuses': automation.status_choices(),
        'targets': TARGETS,
        'rules': [[r.pk, r.name] for r in Rule.objects.exclude(pk=getattr(exclude_rule, 'pk', None))],
        'departments': [],
        'parts': [],
    }
    if apps.is_installed('departments'):
        from core.trees import sorted_by_path
        from departments.models import Department
        result['departments'] = [[d.pk, str(d)] for d in sorted_by_path(Department.objects.select_related('parent'))]
    if apps.is_installed('inventory'):
        from inventory.models import Part
        result['parts'] = [[p.pk, str(p)] for p in Part.objects.filter(is_active=True)]
    return result


def catalogue(rule=None):
    """Everything the builder page needs, as plain data for json_script."""
    return {
        'events': [
            {'key': e.key, 'label': e.label, 'module': _module_title(e.module),
             'fields': [{'key': f.key, 'label': f.label, 'kind': f.kind} for f in e.fields]}
            for e in automation.events().values()
        ],
        'actions': [
            {'key': a.key, 'label': a.label, 'module': _module_title(a.module),
             'params': [{'name': p.name, 'label': p.label, 'type': p.type, 'required': p.required,
                         'choices': [list(c) for c in p.choices], 'help': p.help} for p in a.params]}
            for a in automation.actions().values()
        ],
        'ops': OPS,
        'opsByKind': OPS_BY_KIND,
        'options': options(exclude_rule=rule),
    }


class RuleForm(forms.ModelForm):
    trigger = forms.ChoiceField(label='When')
    # Filled in by the builder's script as JSON.
    conditions = forms.CharField(widget=forms.HiddenInput, required=False)
    actions = forms.CharField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Rule
        fields = ['name', 'description', 'active', 'trigger', 'conditions', 'actions']
        widgets = {'description': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        groups = {}
        for e in automation.events().values():
            groups.setdefault(_module_title(e.module), []).append((e.key, e.label))
        self.fields['trigger'].choices = [('', 'Pick a trigger…')] + list(groups.items())
        if self.instance.pk and not self.is_bound:
            self.initial['conditions'] = json.dumps(self.instance.conditions)
            self.initial['actions'] = json.dumps(self.instance.actions)

    def _json_list(self, name):
        try:
            value = json.loads(self.cleaned_data.get(name) or '[]')
        except ValueError:
            raise forms.ValidationError('The rule builder sent something unreadable; reload the page and try again.')
        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
            raise forms.ValidationError('The rule builder sent something unreadable; reload the page and try again.')
        return value

    def clean_conditions(self):
        return self._json_list('conditions')

    def clean_actions(self):
        return self._json_list('actions')

    def clean(self):
        data = super().clean()
        event = automation.events().get(data.get('trigger'))
        if event and 'conditions' in data:
            data['conditions'] = self._check_conditions(event, data['conditions'])
        if 'actions' in data:
            data['actions'] = self._check_actions(data['actions'])
        return data

    def _check_conditions(self, event, rows):
        fields = {f.key: f for f in event.fields}
        cleaned = []
        for i, row in enumerate(rows, 1):
            if not row.get('field'):
                continue  # an empty row the user never filled in
            field = fields.get(row['field'])
            if field is None:
                self.add_error(None, f'Condition {i}: "{event.label}" has no field "{row["field"]}".')
                continue
            op = row.get('op') or 'is'
            if op not in OPS_BY_KIND.get(field.kind, OPS_BY_KIND['text']):
                self.add_error(None, f'Condition {i}: "{field.label}" can\'t be compared with "{OPS.get(op, op)}".')
                continue
            value = str(row.get('value', '')).strip()
            if field.kind == 'number' or op in ('gt', 'lt'):
                try:
                    Decimal(value)
                except InvalidOperation:
                    self.add_error(None, f'Condition {i}: "{field.label}" needs a number.')
                    continue
            cleaned.append({'field': field.key, 'op': op, 'value': value})
        return cleaned

    def _check_actions(self, rows):
        known = automation.actions()
        opts = options(exclude_rule=None)
        cleaned = []
        for i, row in enumerate(rows, 1):
            action = known.get(row.get('action'))
            if action is None:
                self.add_error(None, f'Step {i}: pick what to do.')
                continue
            params, errors = clean_params(action, row.get('params') or {}, opts)
            for e in errors:
                self.add_error(None, f'Step {i} ({action.label}): {e}')
            cleaned.append({'action': action.key, 'params': params})
        if not cleaned and not self.errors:
            self.add_error(None, 'Add at least one step under "Then".')
        return cleaned


def _ids(values, allowed):
    allowed = {str(v) for v, _ in allowed}
    return [int(v) for v in values if str(v) in allowed]


def clean_params(action, raw, opts):
    """(params, errors): the action's settings, keeping only what it knows about."""
    params, errors = {}, []
    for p in action.params:
        value = raw.get(p.name)
        if p.type == 'bool':
            params[p.name] = bool(value)
            continue
        if p.type == 'users':
            value = _ids(value if isinstance(value, list) else [], opts['users'])
        elif p.type in ('user', 'department', 'part'):
            match = _ids([value], opts[p.type + 's'])
            value = match[0] if match else None
        elif p.type in ('status', 'target', 'select'):
            allowed = {'status': opts['statuses'], 'target': TARGETS, 'select': p.choices}[p.type]
            value = value if value in {v for v, _ in allowed} else None
        elif p.type == 'number':
            try:
                value = str(Decimal(str(value))) if value not in (None, '') else None
            except InvalidOperation:
                errors.append(f'{p.label} must be a number.')
                value = None
        elif p.type == 'task_list':
            value = _clean_task_list(value, opts, p, errors)
        else:
            value = str(value or '').strip()
        if p.required and value in (None, '', []):
            errors.append(f'{p.label} is required.')
        params[p.name] = value
    return params, errors


def _clean_task_list(rows, opts, param, errors):
    tasks = []
    users = {str(v) for v, _ in opts['users']}
    departments = {str(v) for v, _ in opts['departments']}
    priorities = {str(v) for v, _ in param.choices}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not str(row.get('title') or '').strip():
            continue
        assignee = str(row.get('assignee') or '')
        days = str(row.get('due_in_days') or '').strip()
        if days and not days.lstrip('-').isdigit():
            errors.append(f'"{row["title"]}": due in days must be a whole number.')
            days = ''
        tasks.append({
            'title': str(row['title']).strip()[:200],
            'description': str(row.get('description') or '').strip(),
            'assignee': assignee if assignee == '@owner' or assignee in users else '',
            'department': str(row.get('department') or '') if str(row.get('department') or '') in departments else '',
            'priority': str(row.get('priority') or '') if str(row.get('priority') or '') in priorities else '',
            'due_in_days': days,
        })
    return tasks


def describe(rule):
    """Plain-language lines for the rule page: ([condition], [(action, [setting])])."""
    event = automation.events().get(rule.trigger)
    fields = {f.key: f for f in event.fields} if event else {}
    opts = options()
    names = {kind: {str(v): label for v, label in values} for kind, values in opts.items()}
    conditions = []
    for c in rule.conditions:
        field = fields.get(c['field'])
        value = names['rules'].get(str(c['value']), f'rule #{c["value"]}') if field and field.kind == 'rule' else f'"{c["value"]}"'
        conditions.append(f'{field.label if field else c["field"]} {OPS.get(c["op"], c["op"])} {value}')
    known = automation.actions()
    steps = []
    for step in rule.actions:
        action = known.get(step['action'])
        if action is None:
            steps.append((f'{step["action"]} (no longer available)', []))
            continue
        settings = []
        for p in action.params:
            value = step['params'].get(p.name)
            if value in (None, '', [], False):
                continue
            if p.type == 'task_list':
                for t in value:
                    who = 'the owner' if t.get('assignee') == '@owner' else names['users'].get(t.get('assignee'), '')
                    bits = [f'for {who}' if who else '', f'due in {t["due_in_days"]} days' if t.get('due_in_days') else '']
                    settings.append(t['title'] + ''.join(f' · {b}' for b in bits if b))
                continue
            if p.type == 'users':
                shown = ', '.join(names['users'].get(str(v), f'#{v}') for v in value)
            elif p.type in ('user', 'department', 'part'):
                shown = names[p.type + 's'].get(str(value), f'#{value}')
            elif p.type in ('status', 'target', 'select'):
                allowed = {'status': opts['statuses'], 'target': TARGETS, 'select': p.choices}[p.type]
                shown = dict(allowed).get(value, value)
            elif p.type == 'bool':
                shown = 'yes'
            else:
                shown = value
            settings.append(f'{p.label}: {shown}')
        steps.append((action.label, settings))
    return conditions, steps
