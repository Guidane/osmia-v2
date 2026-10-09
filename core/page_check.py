"""Every page of Osmia and its modules, for the all-pages tests (core/test_pages.py
and core/test_browser.py).

``pages(client)`` walks the URL configuration, fills each URL's parameters
with records that exist (``<int:pk>`` on the projects URLs: a project, ...),
and returns the URLs that answer a GET, with how they answered.
"""
import itertools

from django.apps import apps
from django.urls import URLPattern, URLResolver, get_resolver, reverse

# Not opened: they change things on GET, need a file, export the real data folder or are Django's own admin.
SKIPPED = {
    'users:logout', 'core:setup', 'core:image', 'core:data_export_download',
    'core:modules_applying', 'core:modules_status',
}
SKIPPED_NAMESPACES = {'admin'}
TRIES = 3  # records tried per parameter


def _patterns(resolver=None, namespace='', route=''):
    """(full name, route, URLPattern) of every named URL."""
    resolver = resolver or get_resolver()
    for p in resolver.url_patterns:
        if isinstance(p, URLResolver):
            ns = ':'.join(filter(None, [namespace, p.namespace]))
            yield from _patterns(p, ns, route + str(p.pattern))
        elif isinstance(p, URLPattern) and p.name:
            yield (f'{namespace}:{p.name}' if namespace else p.name), route + str(p.pattern), p


def _model_ids(model):
    return list(model._default_manager.order_by('pk').values_list('pk', flat=True)[:TRIES])


def _combinations(namespace, names, view):
    """Parameter values to try together, e.g. [(1, 5), (1, 6)] for ``<int:ct>/<int:pk>``."""
    if names == ['ct', 'pk'] and apps.is_installed('audit'):  # a record's change history
        LogEntry = apps.get_model('audit', 'LogEntry')
        return list(LogEntry.objects.values_list('content_type_id', 'object_id').distinct()[:TRIES])
    return itertools.islice(itertools.product(*[_candidates(namespace, k, view) for k in names]), 30)


def _candidates(namespace, kwarg, view):
    """Values to try for the URL parameter ``kwarg`` of a page in ``namespace``."""
    if kwarg == 'chain_id' and apps.is_installed('audit'):
        LogEntry = apps.get_model('audit', 'LogEntry')
        return list(LogEntry.objects.exclude(chain_id='').values_list('chain_id', flat=True).distinct()[:TRIES])
    if kwarg == 'label':
        return [c.label for c in apps.get_app_configs() if hasattr(c, 'manifest')][:TRIES]
    if kwarg == 'action':
        return ['disable', 'uninstall', 'enable', 'rollback']
    if kwarg == 'model':
        return ['inventory.part', 'tasks.task']
    named = kwarg.removesuffix('_pk').removesuffix('_id')
    models = []
    view_model = getattr(getattr(view, 'view_class', None), 'model', None)
    if kwarg == 'pk' and view_model is not None:
        models.append(view_model)
    if named != 'pk':  # task_pk -> a Task, from whichever module has it
        models += [m for m in apps.get_models() if m._meta.model_name == named]
    try:
        models += list(apps.get_app_config(namespace).get_models())
    except LookupError:
        pass
    if namespace == 'core':
        models += [apps.get_model('core', 'Image')]
    seen, values = set(), []
    for model in models:
        for pk in _model_ids(model):
            if pk not in seen:
                seen.add(pk)
                values.append(pk)
    return values or [1]


def pages(client):
    """[(name, url, status)] of every page, opened with a GET. A page still at 404
    found no record to show (e.g. no harness projects in the data)."""
    found = []
    for name, route, pattern in _patterns():
        namespace = name.rsplit(':', 1)[0] if ':' in name else ''
        if name in SKIPPED or namespace.split(':')[0] in SKIPPED_NAMESPACES:
            continue
        kwargs_names = list(pattern.pattern.converters)
        result = None
        for combo in _combinations(namespace, kwargs_names, pattern.callback):
            url = reverse(name, kwargs=dict(zip(kwargs_names, combo)))
            response = client.get(url)
            result = (name, url, response.status_code)
            if response.status_code != 404:
                break
        if result:
            found.append(result)
    return found
