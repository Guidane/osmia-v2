from collections import Counter

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.conf import settings
from django.db import connection, transaction
from django.db.models import ProtectedError, RestrictedError
from django.db.models.signals import post_delete
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core.management.commands.load_demo import load_demo
from core.modules import get_module

from .models import DemoRecord, demo_objects, is_own


def module_title(label):
    module = get_module(label)
    return f'{module.manifest.icon} {module.manifest.title}' if module else label.capitalize()


def summary(pairs):
    """[(module title, count)] of demo records, by module."""
    counts = Counter(obj._meta.app_label for _, obj in pairs)
    return [(module_title(label), n) for label, n in sorted(counts.items(), key=lambda i: -i[1])]


def removal(user, dry_run):
    """Delete the demo data, newest first, so what was made from other demo
    records (a subtask, a stock move) goes before them. Returns what was
    deleted that isn't demo data itself (records of yours that depended on
    it) and what had to stay. With ``dry_run`` nothing is kept: it's a preview."""
    pairs = demo_objects(keep_user=user)
    demo_keys = {(obj._meta.concrete_model, obj.pk) for _, obj in pairs}
    deleted, own, kept = set(), [], []

    def on_delete(sender, instance, **kwargs):
        key = (sender._meta.concrete_model, instance.pk)
        if key not in demo_keys and key not in deleted:
            own.append(instance)
        deleted.add(key)

    post_delete.connect(on_delete, dispatch_uid='demo-data-removal', weak=False)
    try:
        with transaction.atomic():
            todo = [obj for _, obj in pairs]
            while todo:
                # Something blocked now (a location still holding the stock of a demo
                # part) may go once the rest is gone, so go round until nothing moves.
                blocked = []
                for obj in todo:
                    if (obj._meta.concrete_model, obj.pk) in deleted:
                        continue  # already gone with something it belonged to
                    try:
                        with transaction.atomic():
                            obj.delete()
                    except (ProtectedError, RestrictedError):
                        blocked.append(obj)
                if len(blocked) == len(todo):
                    break
                todo = blocked
            kept = [obj for obj in todo if (obj._meta.concrete_model, obj.pk) not in deleted]  # your records use it
            if dry_run:
                transaction.set_rollback(True)
            else:
                DemoRecord.objects.filter(pk__in=[r.pk for r, obj in pairs
                                                  if (obj._meta.concrete_model, obj.pk) in deleted]).delete()
    finally:
        post_delete.disconnect(dispatch_uid='demo-data-removal')
    if dry_run:  # (after the rollback, so the records they belong to can be read)
        own = [obj for obj in own if is_own(obj)]
    return pairs, own, kept


def backup_db():
    """Back up the database (into data/backups, like module changes do), if
    Osmia is using its own database file. Returns the backup's name or ''."""
    store = getattr(settings, 'ADDONS', None)
    if store is None or str(connection.settings_dict['NAME']) != str(store.db_file):
        return ''
    return store.backup_db('before removing demo data')


@login_required
def index(request):
    pairs = demo_objects(keep_user=request.user)
    return render(request, 'demo_data/index.html', {
        'enabled': bool(pairs), 'count': len(pairs), 'by_module': summary(pairs),
        'can_change': request.user.is_superuser,
    })


@login_required
@require_POST
def load(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    before = DemoRecord.objects.count()
    load_demo()
    added = DemoRecord.objects.count() - before
    messages.success(request, f'Demo data is on: {added} records added.' if added
                     else 'Demo data is on. Everything was there already.')
    return redirect('demo_data:index')


@login_required
def remove(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    if request.method == 'POST':
        backup = backup_db()
        pairs, own, kept = removal(request.user, dry_run=False)
        removed = len(pairs) - len(kept)
        text = f'Demo data is off: {removed} records removed.'
        if kept:
            text += f' {len(kept)} stayed because your own records use them.'
        if backup:
            text += f' The database was backed up first ({backup}).'
        messages.success(request, text)
        return redirect('demo_data:index')
    pairs, own, kept = removal(request.user, dry_run=True)
    return render(request, 'demo_data/remove.html', {
        'count': len(pairs) - len(kept), 'by_module': summary(pairs),
        'own': [(str(o), o._meta.verbose_name) for o in own[:50]], 'own_count': len(own), 'own_more': max(0, len(own) - 50),
        'kept': [(str(o), o._meta.verbose_name) for o in kept[:50]], 'kept_count': len(kept),
    })
