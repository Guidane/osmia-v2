"""Triggers and actions that the Automations module builds rules from.

Modules declare them in their ``hooks.py`` (loaded at startup) without
depending on Automations:

    automation.event('orders.order_placed', 'An order is placed', fields=[...])
    @automation.action('tasks.create_tasks', 'Create tasks', params=[...])
    def create_tasks(ctx, params): ...
    automation.status_model(Order)            # "Change a status" can set it

and announce events as they happen:

    automation.emit('orders.order_placed', order)

``emit`` hands the event to whoever subscribed (the Automations engine); with
Automations not installed it does nothing. Actions get a context with the
record the event is about (``object``), the record the chain started from
(``source``, e.g. the order behind a group of tasks) and the rule.
"""
import threading
from dataclasses import dataclass, field

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db.models.signals import post_init


@dataclass
class Field:
    """Something a rule's condition can test, e.g. an order's supplier.

    kind: text | number | rule (the value is a rule, picked from a list)
    """
    key: str
    label: str
    get: object  # callable(obj) -> value
    kind: str = 'text'


@dataclass
class Event:
    key: str
    label: str
    module: str
    fields: list = field(default_factory=list)


@dataclass
class Param:
    """One setting of an action, shown in the rule builder.

    type: text | textarea | number | select | user | users | department |
          part | status | target | task_list | bool
    """
    name: str
    label: str
    type: str = 'text'
    required: bool = False
    choices: tuple = ()
    help: str = ''


@dataclass
class Action:
    key: str
    label: str
    module: str
    params: list
    run: object  # callable(ctx, params) -> str (what was done, for the run log)


_events = {}
_actions = {}
_status_models = []
_listeners = []
_local = threading.local()
MAX_DEPTH = 5  # rules triggering rules: stop runaway chains


def _module_of(key):
    return key.split('.', 1)[0]


def event(key, label, fields=()):
    _events[key] = Event(key, label, _module_of(key), list(fields))


def action(key, label, params=()):
    def register(fn):
        _actions[key] = Action(key, label, _module_of(key), list(params), fn)
        return fn
    return register


def status_model(model):
    """Records of ``model`` can have their ``status`` set by a rule. A model may
    define ``set_status(value, user=None)`` to do more than save the field
    (e.g. an order that's received books its parts into stock)."""
    if model not in _status_models:
        _status_models.append(model)


def events():
    return dict(_events)


def actions():
    return dict(_actions)


def status_choices():
    """[(value, label)] with value 'app_label.model:status', e.g. 'orders.order:received'."""
    choices = []
    for model in _status_models:
        for value, label in model._meta.get_field('status').choices:
            choices.append((f'{model._meta.label_lower}:{value}', f'{model._meta.verbose_name.title()} → {label}'))
    return choices


def subscribe(listener):
    if listener not in _listeners:
        _listeners.append(listener)


def emit(key, obj, source=None):
    """Announce that event ``key`` happened to ``obj``."""
    depth = getattr(_local, 'depth', 0)
    if depth >= MAX_DEPTH or not _listeners:
        return
    _local.depth = depth + 1
    try:
        for listener in list(_listeners):
            listener(key, obj, source)
    finally:
        _local.depth = depth


# -- Noticing changes -------------------------------------------------------------

def track(model, *field_names):
    """Remember ``field_names`` as loaded, so ``changed()`` can tell whether a
    save changed them (e.g. a task's status becoming "done")."""
    def remember(sender, instance, **kwargs):
        instance._automation_loaded = {name: getattr(instance, name, None) for name in field_names}
    post_init.connect(remember, sender=model, weak=False, dispatch_uid=f'automation-track-{model._meta.label}')


def changed(instance, name):
    """(old, new) if the field changed since the record was loaded, else None."""
    old = getattr(instance, '_automation_loaded', {}).get(name)
    new = getattr(instance, name)
    return (old, new) if old != new else None


def remember_saved(instance, *names):
    """After handling a save, treat the saved values as the new 'loaded' ones."""
    loaded = getattr(instance, '_automation_loaded', {})
    for name in names:
        loaded[name] = getattr(instance, name)
    instance._automation_loaded = loaded


# -- Text with placeholders -------------------------------------------------------

def render(text, ctx):
    """Fill {object} and {source} (the records' names) into a text."""
    return (text or '').replace('{object}', str(ctx.get('object', ''))).replace('{source}', str(ctx.get('source', '')))


# -- The built-in "Change a status" action ------------------------------------------

@action('core.set_status', 'Change a status', params=[
    Param('target', 'Of', 'target', required=True,
          help='The record the rule was triggered by, or the record the chain started from (e.g. the order behind a group of tasks).'),
    Param('status', 'To', 'status', required=True),
])
def set_status(ctx, params):
    target = ctx.get(params.get('target') or 'object')
    model_label, _, value = (params.get('status') or '').partition(':')
    if target is None or target._meta.label_lower != model_label:
        wanted = apps.get_model(model_label)._meta.verbose_name if model_label else 'record'
        raise ValidationError(f'This rule can only change the status of a {wanted}, not of "{target}".')
    if hasattr(target, 'set_status'):
        target.set_status(value, user=None)
    else:
        target.status = value
        target.save()
    return f'Set {target} to {target.get_status_display() if hasattr(target, "get_status_display") else value}'
