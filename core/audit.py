"""The change log: every create, change and delete of a module's records.

The Audit module stores and shows the entries (it calls ``connect()`` at
startup); without it nothing is logged, but ``acting()`` still works, so
modules can use it without depending on Audit. Changes are picked up from
model signals, so modules don't have to do anything. Each entry records which module's code made the change: a web
request acts as the module whose page it is, and code that works on behalf
of another module says so with ``acting()``:

    with audit.acting('automations', source='rule "Check in placed orders"'):
        ...  # tasks created here are logged under Tasks, tagged ⛓ automations

Everything done within one request (or one ``acting()`` from a script)
shares a **chain id**, so a change can be traced across modules.

A model can opt out with ``audit_log = False`` and leave fields out with
``audit_ignore = ('field', ...)``. Child records (an order's lines, a
device's pins) are logged on their parent's page: the parent is the
``audit_record()`` a model defines, or else its first foreign key to a
record of the same module that has a page.
"""
import threading
import uuid
from contextlib import contextmanager

from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete, post_save, pre_save

_local = threading.local()

ALWAYS_IGNORED = {'last_login'}  # passwords are logged as "(changed)", never their value
SECRET_FIELDS = {'password'}
MAX_VALUE = 200


# -- Who is acting ---------------------------------------------------------------

def _stack():
    if not hasattr(_local, 'frames'):
        _local.frames = []
    return _local.frames


def new_chain_id():
    return uuid.uuid4().hex[:10]


@contextmanager
def acting(module, source='', user=None):
    """Code inside this block acts as ``module``: changes it makes to other
    modules' records are tagged with it. Starts a chain if none is running."""
    frames = _stack()
    parent = frames[-1] if frames else None
    frames.append({
        'module': module,
        'source': source or (parent['source'] if parent else ''),
        'user': user if user is not None else (parent['user'] if parent else None),
        'chain_id': parent['chain_id'] if parent else new_chain_id(),
    })
    try:
        yield frames[-1]
    finally:
        frames.pop()


def current():
    frames = _stack()
    return frames[-1] if frames else None


def chain_path():
    """The modules the chain went through so far, e.g. ['tasks', 'automations']."""
    path = []
    for f in _stack():
        if f['module'] and (not path or path[-1] != f['module']):
            path.append(f['module'])
    return path


class AuditMiddleware:
    """Each request is a chain, acting as the module whose page it is."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.urls import Resolver404, resolve
        try:
            module = resolve(request.path_info).namespace or 'core'
        except Resolver404:
            module = 'core'
        user = request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
        with acting(module, source='admin' if module == 'admin' else '', user=user):
            return self.get_response(request)


# -- What changed ------------------------------------------------------------------

def _logged_models():
    from django.apps import apps
    from osmia.addons import BUILTIN
    labels = (set(getattr(settings, 'OSMIA_MODULES', ())) | {'core', *BUILTIN}) - {'audit'}
    for model in apps.get_models():
        if model._meta.app_label in labels and getattr(model, 'audit_log', True):
            yield model


def _fields(model):
    ignored = ALWAYS_IGNORED | set(getattr(model, 'audit_ignore', ()))
    for f in model._meta.concrete_fields:
        if f.primary_key or f.name in ignored or getattr(f, 'auto_now', False) or getattr(f, 'auto_now_add', False):
            continue
        yield f


def _display(field, value):
    if value is None or value == '':
        return ''
    if field.name in SECRET_FIELDS:
        return '•••'
    if field.is_relation:
        try:
            obj = field.related_model._base_manager.filter(pk=value).first()
        except Exception:
            obj = None
        return _label(obj) if obj is not None else f'#{value}'
    if field.flatchoices:
        return str(dict(field.flatchoices).get(value, value))
    if isinstance(field, models.BooleanField):
        return 'yes' if value else 'no'
    if isinstance(field, (models.JSONField, models.FileField)):
        return '(updated)' if value else ''
    text = str(value)
    return text if len(text) <= MAX_VALUE else text[:MAX_VALUE] + '…'


def _values(instance):
    return {f.attname: getattr(instance, f.attname) for f in _fields(type(instance))}


def _diff(model, old, new):
    changes = []
    for f in _fields(model):
        a, b = old.get(f.attname), new.get(f.attname)
        if a == b or (a in (None, '') and b in (None, '')):
            continue
        entry = {'field': str(f.verbose_name), 'old': _display(f, a), 'new': _display(f, b)}
        if isinstance(f, models.JSONField) or f.name in SECRET_FIELDS:
            entry['old'], entry['new'] = '', '(changed)'
        changes.append(entry)
    return changes


def _owner(instance):
    """(module label, record) the entry belongs to: the instance itself, or
    the parent record for child rows such as an order's lines."""
    owner = instance.audit_record() if hasattr(instance, 'audit_record') else None
    if owner is None and not hasattr(instance, 'get_absolute_url'):
        for f in instance._meta.concrete_fields:
            if (f.many_to_one and f.related_model._meta.app_label == instance._meta.app_label
                    and hasattr(f.related_model, 'get_absolute_url')):
                owner = getattr(instance, f.name, None)
                if owner is not None:
                    break
    return owner if owner is not None else instance


def _label(obj):
    try:
        return str(obj)[:200]
    except Exception:
        return f'{obj._meta.verbose_name} #{obj.pk}'


def _url(obj):
    try:
        return obj.get_absolute_url()
    except Exception:
        return ''


def log(instance, action, changes=()):
    """Record an entry for ``instance``. Also usable directly for changes that
    bypass save(), e.g. queryset.update()."""
    from django.apps import apps
    from django.contrib.contenttypes.models import ContentType

    if not apps.is_installed('audit'):
        return
    LogEntry = apps.get_model('audit', 'LogEntry')
    owner = _owner(instance)
    frame = current()
    module = owner._meta.app_label
    is_child = owner is not instance
    path = chain_path()
    actor = frame['module'] if frame else ''
    try:
        LogEntry.objects.create(
            module=module,
            content_type=ContentType.objects.get_for_model(owner),
            object_id=owner.pk or 0,
            object_label=_label(owner),
            object_url=_url(owner)[:300],
            item_type=str(instance._meta.verbose_name) if is_child else '',
            item_label=_label(instance) if is_child else '',
            action=action,
            changes=list(changes),
            user=frame['user'] if frame else None,
            actor_module=actor,
            chain_path=' > '.join(path)[:200],
            chain_id=frame['chain_id'] if frame else '',
            source=(frame['source'] if frame else '')[:200],
        )
    except Exception:  # the log must never break the change it describes
        import logging
        logging.getLogger(__name__).exception('Could not write a log entry for %s #%s', instance._meta.label, instance.pk)


def log_change(instance, **fields):
    """Log field changes made without save(): log_change(part, quantity_on_hand=(old, new))."""
    model = type(instance)
    by_name = {f.name: f for f in model._meta.concrete_fields}
    changes = [{'field': str(by_name[name].verbose_name) if name in by_name else name,
                'old': _display(by_name[name], old) if name in by_name else str(old),
                'new': _display(by_name[name], new) if name in by_name else str(new)}
               for name, (old, new) in fields.items() if old != new]
    if changes:
        log(instance, 'changed', changes)


def _pre_save(sender, instance, raw=False, **kwargs):
    if raw or instance.pk is None:
        instance._audit_old = None
        return
    names = [f.attname for f in _fields(sender)]
    instance._audit_old = sender._base_manager.filter(pk=instance.pk).values(*names).first()


def _post_save(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    old = getattr(instance, '_audit_old', None)
    if created or old is None:
        changes = _diff(sender, {}, _values(instance))
        log(instance, 'created', changes)
    else:
        changes = _diff(sender, old, _values(instance))
        if changes:
            log(instance, 'changed', changes)
    instance._audit_old = None


def _post_delete(sender, instance, **kwargs):
    log(instance, 'deleted')


def connect():
    for model in _logged_models():
        uid = f'audit-{model._meta.label}'
        pre_save.connect(_pre_save, sender=model, dispatch_uid=uid + '-pre')
        post_save.connect(_post_save, sender=model, dispatch_uid=uid + '-post')
        post_delete.connect(_post_delete, sender=model, dispatch_uid=uid + '-del')
