"""
The Modules page: install, upgrade, switch off and remove modules from the website.

Nothing here changes the running site. Each action queues a change in the
add-on store (osmia/addons.py) and asks for a restart; the change is applied
on the way back up, before Django loads, and undone completely if it fails.
"""
import os
import shutil
import threading
from importlib.util import find_spec
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from osmia import addons


def superuser_required(view):
    return user_passes_test(lambda u: u.is_active and u.is_superuser)(view)


def _store():
    return settings.ADDONS


def can_change():
    """Modules run from source in development (OSMIA_DEV_MODULES): nothing to install."""
    return not settings.OSMIA_DEV


def supervised():
    """Is something waiting to start Osmia again when it exits (serve.py or runserver's reloader)?"""
    return os.environ.get('OSMIA_SERVE') == '1' or os.environ.get('RUN_MAIN') == 'true'


def request_restart():
    """Exit shortly (after this response is sent) so the supervisor starts Osmia
    again and the queued change is applied. False if nothing would restart it."""
    if not supervised():
        return False
    threading.Timer(1.0, os._exit, (addons.RESTART_EXIT_CODE,)).start()
    return True


# -- Planning changes --------------------------------------------------------------

def installed_modules_state():
    """{label: entry} as the page shows it: the store's, or the source modules in development."""
    if can_change():
        return _store().installed()
    return {label: {**m, 'enabled': label in settings.OSMIA_MODULES}
            for label, m in addons.bundled_manifests().items()}


def _ver(manifest):
    return addons.parse_version(manifest['version'])


def missing_dependencies(labels, manifests, installed):
    """The modules ``labels`` need that aren't installed, with their own needs,
    in install order. Raises PackageError if one isn't available anywhere."""
    bundled = addons.bundled_manifests()
    wanted = {}

    def visit(label, needed_by):
        if label in wanted or label in addons.BUILTIN:
            return
        if label in installed and label not in labels:
            if not installed[label].get('enabled', True):
                raise addons.PackageError(f'{needed_by} needs {installed[label]["title"]}, which is switched off. Enable it first.')
            return
        manifest = manifests.get(label) or bundled.get(label)
        if manifest is None:
            raise addons.PackageError(f'{needed_by} needs a module called "{label}", which Osmia doesn\'t have. Install it first.')
        wanted[label] = manifest
        for dep in manifest['depends']:
            visit(dep, manifest['title'])

    for label in labels:
        visit(label, manifests.get(label, bundled.get(label, {})).get('title', label))
    return addons.dependency_order(wanted)


def _taken_by_python(label):
    """Is ``label`` the name of a Python package (json, email, ...) that a module would hide?"""
    spec = find_spec(label)
    if spec is None:
        return False
    ours = (addons.BUNDLED_DIR.resolve(), _store().modules_dir.resolve())
    places = list(spec.submodule_search_locations or []) or [spec.origin or '']
    return not all(any(Path(p).resolve().is_relative_to(d) for d in ours) for p in places if p)


def _check_installable(manifest, installed):
    label = manifest['label']
    if label in addons.RESERVED:
        raise addons.PackageError(f'"{label}" is a name Osmia uses itself; give the module another label.')
    if label not in installed and _taken_by_python(label):
        raise addons.PackageError(f'"{label}" is already the name of a Python package on this server; '
                                  'give the module another label.')
    missing = addons.missing_requirements(manifest)
    if missing:
        raise addons.PackageError(f'{manifest["title"]} needs Python packages that aren\'t installed: '
                                  f'{", ".join(missing)}. Install them on the server (pip install ...) first.')


def stage_bundled(label, installed):
    """Copy a module that comes with Osmia to the staging area; the install op for it."""
    store = _store()
    manifest = addons.read_manifest(addons.BUNDLED_DIR / label)
    _check_installable(manifest, installed)
    staging = store.new_staging()
    shutil.copytree(addons.BUNDLED_DIR / label, staging / label,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    return _install_op(manifest, staging, installed)


def _install_op(manifest, staging, installed):
    return {'op': 'install', 'label': manifest['label'], 'title': manifest['title'], 'version': manifest['version'],
            'from_version': installed.get(manifest['label'], {}).get('version'),
            'staging': staging.relative_to(_store().root).as_posix()}


def plan_bundled_install(labels):
    """Install ops for bundled modules ``labels`` plus whatever they need."""
    installed = _store().installed()
    order = missing_dependencies(labels, {}, installed)
    return [stage_bundled(label, installed) for label in order]


def plan_upload(uploaded):
    """Install ops for an uploaded module ``.zip`` (and any bundled modules it needs)."""
    store, installed = _store(), _store().installed()
    package = addons.read_package(uploaded)
    manifest = package.manifest
    _check_installable(manifest, installed)
    current = installed.get(manifest['label'])
    if current and _ver(manifest) < _ver(current):
        raise addons.PackageError(f'{manifest["title"]} {current["version"]} is installed; this package is the older '
                                  f'{manifest["version"]}. Use Roll back to return to an earlier version.')
    needed = missing_dependencies([manifest['label']], {manifest['label']: manifest}, installed)
    ops = [stage_bundled(label, installed) for label in needed if label != manifest['label']]
    staging = store.new_staging()
    uploaded.seek(0)
    addons.extract_package(uploaded, package, staging / manifest['label'])
    ops.append(_install_op(manifest, staging, installed))
    return ops


def dependents(label, installed, enabled_only=False):
    """Titles of the installed modules that need ``label``."""
    return [e['title'] for l, e in installed.items()
            if label in e.get('depends', []) and (e.get('enabled', True) or not enabled_only)]


def queue_change(request, ops, demo=()):
    store = _store()
    try:
        store.queue(ops, user=request.user.username, demo=demo)
    except addons.PackageError:
        for op in ops:  # nothing will use what was staged for them
            if op.get('staging'):
                addons._rmtree(store.root / op['staging'])
        raise
    request_restart()
    return redirect('core:modules_applying')


# -- Pages -------------------------------------------------------------------------

@superuser_required
def module_list(request):
    store = _store()
    installed = installed_modules_state()
    bundled = addons.bundled_manifests()
    rows = []
    for label in addons.dependency_order(installed) if installed else []:
        entry = installed[label]
        newer = bundled.get(label)
        rows.append({
            'label': label, 'entry': entry,
            'loaded': label in settings.OSMIA_MODULES,
            'update': newer['version'] if can_change() and newer and _ver(newer) > _ver(entry) else '',
            'previous': (entry.get('previous') or [None])[0],
            'needed_by': dependents(label, installed),
        })
    available = [m for label, m in sorted(bundled.items(), key=lambda i: (i[1]['sequence'], i[0])) if label not in installed]
    results = store.results()[:10] if can_change() else []
    done = next((r for r in results if r['id'] == request.GET.get('done')), None)
    if done:  # back from the "Applying changes" page
        if done['ok']:
            messages.success(request, 'Done: ' + '; '.join(done['changes']) + '.')
        else:
            messages.error(request, 'That change failed, so everything was put back as it was. The details are under Recent changes.')
    return render(request, 'core/modules.html', {
        'rows': rows, 'available': available, 'can_change': can_change(), 'supervised': supervised(),
        'pending': store.pending() if can_change() else None, 'results': results,
        'updates': [r['label'] for r in rows if r['update']],
        'osmia_version': addons.OSMIA_VERSION, 'safe_mode': settings.OSMIA_SAFE_MODE,
        'data_dir': settings.DATA_DIR, 'highlight': request.GET.get('done', ''),
    })


def _changes_allowed(request):
    if not can_change():
        messages.error(request, 'Osmia is running its modules from source (OSMIA_DEV_MODULES), so they can\'t be changed here.')
        return False
    if _store().pending():
        messages.error(request, 'A change is already waiting to be applied.')
        return False
    return True


@superuser_required
@require_POST
def module_install(request):
    """Install bundled modules (from the "Available" list) or upgrade them to the bundled version."""
    if not _changes_allowed(request):
        return redirect('core:modules')
    labels = [l for l in request.POST.getlist('module') if l]
    if not labels:
        messages.error(request, 'Pick a module to install.')
        return redirect('core:modules')
    try:
        ops = plan_bundled_install(labels)
        if not ops:
            messages.info(request, 'Those modules are already installed.')
            return redirect('core:modules')
        demo = [op['label'] for op in ops if not op.get('from_version')] if request.POST.get('demo') else []
        return queue_change(request, ops, demo)
    except addons.PackageError as exc:
        messages.error(request, str(exc))
        return redirect('core:modules')


@superuser_required
@require_POST
def module_upload(request):
    if not _changes_allowed(request):
        return redirect('core:modules')
    uploaded = request.FILES.get('package')
    if uploaded is None:
        messages.error(request, 'Pick a module .zip file to upload.')
        return redirect('core:modules')
    try:
        ops = plan_upload(uploaded)
        demo = [op['label'] for op in ops if not op.get('from_version')] if request.POST.get('demo') else []
        return queue_change(request, ops, demo)
    except addons.PackageError as exc:
        messages.error(request, f'{uploaded.name}: {exc}')
        return redirect('core:modules')


@superuser_required
def module_change(request, label, action):
    """Enable, disable, uninstall or roll back one module; GET asks to confirm."""
    installed = installed_modules_state()
    entry = installed.get(label)
    if entry is None or action not in ('enable', 'disable', 'uninstall', 'rollback'):
        raise Http404
    problem = ''
    if action in ('disable', 'uninstall'):
        needed_by = dependents(label, installed, enabled_only=action == 'disable')
        if needed_by:
            problem = f'{", ".join(needed_by)} need{"s" if len(needed_by) == 1 else ""} {entry["title"]}. ' \
                      f'{"Disable" if action == "disable" else "Uninstall"} {"it" if len(needed_by) == 1 else "them"} first.'
    elif action == 'enable':
        off = [installed[d]['title'] for d in entry.get('depends', []) if d in installed and not installed[d].get('enabled', True)]
        if off:
            problem = f'{entry["title"]} needs {", ".join(off)}. Enable {"it" if len(off) == 1 else "them"} first.'
    elif action == 'rollback' and not entry.get('previous'):
        problem = f'There is no earlier version of {entry["title"]} to go back to.'
    if request.method != 'POST':
        return render(request, 'core/module_confirm.html', {
            'entry': entry, 'label': label, 'action': action, 'problem': problem,
            'previous': (entry.get('previous') or [None])[0], 'can_change': can_change(),
        })
    if problem:
        messages.error(request, problem)
        return redirect('core:modules')
    if not _changes_allowed(request):
        return redirect('core:modules')
    op = {'op': action, 'label': label, 'title': entry['title']}
    if action == 'uninstall':
        op['delete_data'] = request.POST.get('delete_data') == '1'
    return queue_change(request, [op])


@superuser_required
@require_POST
def module_update_all(request):
    if not _changes_allowed(request):
        return redirect('core:modules')
    installed, bundled = _store().installed(), addons.bundled_manifests()
    labels = [l for l, e in installed.items() if l in bundled and _ver(bundled[l]) > _ver(e)]
    try:
        ops = [stage_bundled(label, installed) for label in addons.dependency_order({l: bundled[l] for l in labels})]
        if not ops:
            messages.info(request, 'Every module is up to date.')
            return redirect('core:modules')
        return queue_change(request, ops)
    except addons.PackageError as exc:
        messages.error(request, str(exc))
        return redirect('core:modules')


@superuser_required
@require_POST
def module_cancel(request):
    _store().cancel_pending()
    messages.success(request, 'The waiting change was cancelled.')
    return redirect('core:modules')


@superuser_required
def module_download(request, label):
    """The module as a .zip, e.g. to install it on another Osmia."""
    folder = (_store().modules_dir if can_change() else addons.BUNDLED_DIR) / label
    if not addons.LABEL_RE.match(label) or not (folder / 'manifest.json').is_file():
        raise Http404
    version = addons.read_manifest(folder)['version']
    response = HttpResponse(addons.zip_folder(folder), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{label}-{version}.zip"'
    return response


@superuser_required
def module_applying(request):
    """Shown while Osmia restarts to apply a change; reloads itself when it's done."""
    store = _store()
    return render(request, 'core/module_applying.html', {
        'pending': store.pending(), 'supervised': supervised(),
        'last': (store.results() or [{}])[0].get('id', ''),
    })


@superuser_required
def module_status(request):
    store = _store()
    last = (store.results() or [{}])[0]
    return JsonResponse({'pending': store.pending() is not None, 'result': last.get('id', ''), 'ok': last.get('ok')})
