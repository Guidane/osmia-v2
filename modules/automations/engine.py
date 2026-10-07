"""Runs the rules whose trigger just happened (subscribed to core.automation)."""
import logging
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from core import audit, automation

from .models import Rule, Run

log = logging.getLogger(__name__)

OPS = {
    'is': 'is',
    'is_not': 'is not',
    'contains': 'contains',
    'gt': 'is more than',
    'lt': 'is less than',
}


def _number(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def test(condition, fields, obj):
    """Does ``obj`` pass one condition? Unknown fields never pass."""
    field = fields.get(condition.get('field'))
    if field is None:
        return False
    op, wanted = condition.get('op', 'is'), condition.get('value', '')
    actual = field.get(obj)
    if op in ('gt', 'lt'):
        a, b = _number(actual), _number(wanted)
        if a is None or b is None:
            return False
        return a > b if op == 'gt' else a < b
    actual, wanted = str(actual if actual is not None else '').strip().lower(), str(wanted).strip().lower()
    if op == 'is':
        return actual == wanted
    if op == 'is_not':
        return actual != wanted
    if op == 'contains':
        return wanted in actual
    return False


def matches(rule, obj):
    event = automation.events().get(rule.trigger)
    fields = {f.key: f for f in event.fields} if event else {}
    return all(test(c, fields, obj) for c in rule.conditions)


def _url(record):
    try:
        return record.get_absolute_url() if record is not None else ''
    except Exception:
        return ''


def run_rule(rule, event_key, obj, source=None):
    """Run a rule's actions for ``obj``. Either all of them take effect or,
    if one fails, none do; the triggering change itself is kept either way."""
    source = source if source is not None else getattr(obj, 'automation_source', None)
    ctx = {'object': obj, 'source': source if source is not None else obj, 'rule': rule}
    known = automation.actions()
    lines, status = [], Run.Status.OK
    try:
        with audit.acting('automations', source=f'rule "{rule.name}"'), transaction.atomic():
            for step in rule.actions:
                action = known.get(step.get('action'))
                if action is None:
                    raise ValidationError(f'The action "{step.get("action")}" is no longer available.')
                lines.append(action.run(ctx, step.get('params') or {}) or action.label)
    except Exception as exc:  # a broken rule must never break the page that triggered it
        status = Run.Status.FAILED
        message = ' '.join(exc.messages) if isinstance(exc, ValidationError) else f'{type(exc).__name__}: {exc}'
        lines.append(f'Failed: {message}' + ('' if not lines else ' (the steps above were undone)'))
        if not isinstance(exc, ValidationError):
            log.exception('Automation rule %s failed', rule.pk)
    return Run.objects.create(
        rule=rule, event=event_key, status=status, log='\n'.join(lines),
        object_label=str(obj)[:200], object_url=_url(obj)[:300],
        source_label=str(source)[:200] if source is not None else '', source_url=_url(source)[:300],
    )


def handle(event_key, obj, source=None):
    for rule in Rule.objects.filter(active=True, trigger=event_key):
        if matches(rule, obj):
            run_rule(rule, event_key, obj, source)
