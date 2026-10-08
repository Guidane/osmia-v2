"""Which records are demo data, so they can be removed again.

A record created while demo data loads (``core`` load_demo runs each module's
``demo.load()`` inside ``audit.acting(..., source='demo data')``) is noted
here, whichever way the loading was started: this module's page, the setup
page, "with demo data" on the Modules page or ``manage.py load_demo``.
Demo data loaded before this module was installed is found in the Audit
module's change log, which tags those changes with the same source.
"""
import re

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core import audit

SOURCE = 'demo data'


class DemoRecord(models.Model):
    content_type = models.ForeignKey('contenttypes.ContentType', on_delete=models.CASCADE, related_name='+')
    object_id = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(default=timezone.now)  # removal goes newest first

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [models.UniqueConstraint(fields=['content_type', 'object_id'], name='unique_demo_record')]

    def __str__(self):
        return f'{self.content_type.app_label}.{self.content_type.model} #{self.object_id}'


class LogMark(models.Model):
    """How far the change log has been read for earlier demo data (one row)."""
    last_entry_id = models.PositiveBigIntegerField(default=0)


def tracked_labels():
    """The apps whose records can be demo data: Osmia's modules, users and core."""
    from osmia.addons import BUILTIN
    return (set(getattr(settings, 'OSMIA_MODULES', ())) | {'core', *BUILTIN}) - {'demo_data', 'audit'}


def track(sender, instance, created, raw=False, **kwargs):
    """post_save of every model: note what demo loading creates."""
    if not created or raw:
        return
    # Also what demo data sets off, e.g. tasks an automation rule makes for a demo order.
    if not any(f['source'] == SOURCE for f in audit._stack()) or sender._meta.app_label not in tracked_labels():
        return
    DemoRecord.objects.get_or_create(content_type=ContentType.objects.get_for_model(sender), object_id=instance.pk)


def adopt_from_audit():
    """Note demo data loaded before this module was installed, from the change
    log. Only entries for the record itself: an entry for a child row (an
    order's line) points at the parent, which may not be demo data."""
    if not apps.is_installed('audit'):
        return
    LogEntry = apps.get_model('audit', 'LogEntry')
    mark, _ = LogMark.objects.get_or_create(pk=1)
    # Everything in a chain that loaded demo data, including what it set off (an
    # automation rule's tasks are logged under the rule, in the same chain).
    chains = LogEntry.objects.filter(source=SOURCE, pk__gt=mark.last_entry_id).exclude(chain_id='').values('chain_id')
    created = LogEntry.objects.filter(Q(source=SOURCE) | Q(chain_id__in=chains), action='created',
                                      content_type__isnull=False, pk__gt=mark.last_entry_id)
    last = created.order_by('-pk').values_list('pk', flat=True).first()
    if last is None:
        return
    entries = list(created.filter(item_type='').values_list('content_type_id', 'object_id', 'created_at'))
    entries += child_entries(created.exclude(item_type=''))
    known = set(DemoRecord.objects.values_list('content_type_id', 'object_id'))
    new = {}
    for ct_id, object_id, created_at in entries:
        if (ct_id, object_id) not in known:
            new.setdefault((ct_id, object_id), created_at)
    LogMark.objects.filter(pk=1).update(last_entry_id=last)
    if not new:
        return
    # Only records that still exist (ids aren't reused, but deleted ones needn't be listed).
    by_type = {}
    for ct_id, object_id in new:
        by_type.setdefault(ct_id, set()).add(object_id)
    rows = []
    for ct_id, ids in by_type.items():
        model = ContentType.objects.get_for_id(ct_id).model_class()
        if model is None or model._meta.app_label not in tracked_labels():
            continue
        for pk in model._base_manager.filter(pk__in=ids).values_list('pk', flat=True):
            rows.append(DemoRecord(content_type_id=ct_id, object_id=pk, created_at=new[(ct_id, pk)]))
    DemoRecord.objects.bulk_create(rows, ignore_conflicts=True)


def same_label(a, b):
    """Labels equal but for how numbers are written: "+3 pcs" is "+3.00 pcs"."""
    def plain(text):
        return re.sub(r'(\d+)\.(\d*?)0*(?!\d)', lambda m: m.group(1) + ('.' + m.group(2) if m.group(2) else ''), text)
    return plain(a) == plain(b)


def child_entries(entries):
    """(content type id, id, created) of the child rows behind log entries that
    are shown on their parent (e.g. a stock move on its part's page). A row
    counts when it's of the logged kind, points at the parent, has the logged
    label and was made within a minute of the entry: then it's surely that one."""
    from datetime import timedelta
    by_kind = {}
    for model in apps.get_models():
        if model._meta.app_label in tracked_labels():
            by_kind.setdefault(str(model._meta.verbose_name), []).append(model)
    found = []
    for e in entries.select_related('content_type'):
        owner = e.content_type.model_class()
        for model in by_kind.get(e.item_type, ()):
            links = [f.attname for f in model._meta.concrete_fields
                     if f.many_to_one and owner is not None and issubclass(owner, f.related_model)]
            if not links:
                continue
            qs = model._base_manager.none()
            for attname in links:
                qs = qs | model._base_manager.filter(**{attname: e.object_id})
            timed = any(f.name == 'created_at' for f in model._meta.concrete_fields)
            if timed:
                qs = qs.filter(created_at__range=(e.created_at - timedelta(minutes=1), e.created_at + timedelta(minutes=1)))
            candidates = list(qs[:20])
            # The label may have changed since (a quantity, "8" saved as "8.00"): then the only
            # row of its kind on that parent made within that minute is the one.
            matches = [o for o in candidates if same_label(audit._label(o), e.item_label)] or (
                candidates if timed and len(candidates) == 1 else [])
            for obj in matches:
                found.append((ContentType.objects.get_for_model(model).pk, obj.pk, e.created_at))
    return found


def is_own(obj):
    """Was ``obj`` (not noted as demo data, but going with it) made by someone
    rather than by demo loading? Asked of records that a removal would take
    along. Bookkeeping that's never logged (automation runs, notifications)
    isn't anyone's own; otherwise the change log decides, and without one, it is."""
    if not getattr(type(obj), 'audit_log', True) or obj._meta.app_label not in tracked_labels():
        return False
    if not apps.is_installed('audit'):
        return True
    LogEntry = apps.get_model('audit', 'LogEntry')
    try:
        owner = audit._owner(obj)
    except Exception:
        owner = obj
    if owner is obj:
        entries = LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk, item_type='')
    else:
        entries = LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(owner), object_id=owner.pk,
                                          item_type=str(obj._meta.verbose_name))
    demo_chains = LogEntry.objects.filter(source=SOURCE).exclude(chain_id='').values('chain_id')
    created = entries.filter(action='created')
    return not created.exists() or created.exclude(Q(source=SOURCE) | Q(chain_id__in=demo_chains)).exists()


def demo_objects(keep_user=None):
    """[(record, object)] of the demo data that still exists, newest first.
    Administrators and ``keep_user`` are never demo data to remove."""
    adopt_from_audit()
    records = list(DemoRecord.objects.select_related('content_type').order_by('-created_at', '-id'))
    by_type = {}
    for r in records:
        by_type.setdefault(r.content_type_id, set()).add(r.object_id)
    objects = {}
    for ct_id, ids in by_type.items():
        model = ContentType.objects.get_for_id(ct_id).model_class()
        if model is not None:
            for obj in model._base_manager.filter(pk__in=ids):
                objects[(ct_id, obj.pk)] = obj
    gone = [r.pk for r in records if (r.content_type_id, r.object_id) not in objects]
    if gone:
        DemoRecord.objects.filter(pk__in=gone).delete()
    result = []
    for r in records:
        obj = objects.get((r.content_type_id, r.object_id))
        if obj is None or getattr(obj, 'is_superuser', False) or (keep_user is not None and obj == keep_user):
            continue
        result.append((r, obj))
    return result
