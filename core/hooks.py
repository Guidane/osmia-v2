"""
Extension points that let modules contribute to each other without importing
each other. A module registers callbacks in its own ``hooks.py`` (discovered
automatically at startup); the page that owns the extension point calls
``hooks.collect(name, ...)``.

Hooks used by the bundled modules:

    dashboard_widgets(request)        -> Widget
    user_detail_panels(request, user) -> Panel
    task_detail_panels(request, task) -> Panel
    part_detail_panels(request, part) -> Panel
    department_detail_panels(request, department) -> Panel
    assembly_detail_panels(request, assembly) -> Panel
    device_detail_panels(request, device) -> Panel
    task_costs(task_ids)              -> Costs  (money spent per task, e.g. materials)
    budget_costs(budget_ids)          -> Costs  (money spent straight on a budget, e.g. orders; by_task holds budget ids)
    budget_detail_panels(request, budget), order_detail_panels(request, order) -> Panel
    topbar_items(request)             -> safe HTML for the top bar (e.g. the notifications bell)
    module_log_url(request, module)   -> URL of the "Log" link in a module's menu (Audit)

The Users pages are built in, so modules add to them with hooks rather than fields:

    user_detail_facts(request, user)   -> Fact      (a line in the user's details)
    user_list_columns(request)         -> Column    (a column of the user list)
    user_list_filters(request)         -> ListFilter (a filter above the user list)
    user_form_extensions(request, user) -> a Form with save(user); user is None on "New user"

A callback may return None to contribute nothing.
"""
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from django.template.loader import render_to_string

_registry = defaultdict(list)


@dataclass
class Widget:
    title: str
    value: object
    url: str = ""
    tone: str = ""  # "", "warn" or "ok"


@dataclass
class Panel:
    title: str
    html: str
    order: int = 100


@dataclass
class Costs:
    label: str
    by_task: dict  # {task id: Decimal}


@dataclass
class Fact:
    label: str
    html: str  # safe HTML
    order: int = 100


@dataclass
class Column:
    title: str
    cells: Callable  # (list of records) -> {pk: safe HTML}, one query for the page
    order: int = 100


@dataclass
class ListFilter:
    html: str  # safe HTML of form controls, placed inside the list's GET form
    apply: Callable  # (queryset) -> filtered queryset
    order: int = 100


def register(name):
    def decorator(fn):
        _registry[name].append(fn)
        return fn
    return decorator


def collect(name, *args, **kwargs):
    results = [fn(*args, **kwargs) for fn in _registry[name]]
    results = [r for r in results if r is not None]
    return sorted(results, key=lambda r: getattr(r, "order", 100))


def panel(title, template, context, request=None, order=100):
    return Panel(title=title, html=render_to_string(template, context, request=request), order=order)
