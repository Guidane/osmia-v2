"""Projects on the pages of the modules it ties together: tasks, departments,
budgets, orders and users. Orders and Stock aren't dependencies, so their
parts only show when they're installed."""
from django.apps import apps
from django.db.models import Q
from django.urls import reverse

from budgets.models import Spending
from core import hooks

from .forms import TaskProjectForm
from .models import Project
from .views import annotate, can_edit, with_counts

if apps.is_installed('audit'):  # the History panel, as on the other modules' pages
    from audit.hooks import history_panel
    hooks.register('project_detail_panels')(history_panel)


def project_of_budget(budget):
    """The project whose budget is ``budget`` or one above it (the nearest), if any."""
    if budget is None:
        return None
    chain = [b.pk for b in reversed(budget.ancestors())]
    projects = {p.budget_id: p for p in Project.objects.filter(budget_id__in=chain)}
    return next((projects[pk] for pk in chain if pk in projects), None)


@hooks.register('dashboard_widgets')
def open_projects(request):
    projects = annotate(list(with_counts(Project.objects.filter(status__in=Project.OPEN))))
    over = sum(1 for p in projects if p.budget_id and p.budget.over)
    title = f'Open projects ({over} over budget)' if over else 'Open projects'
    return hooks.Widget(title, len(projects), reverse('projects:list'), tone='warn' if over else '')


@hooks.register('task_detail_panels')
def task_project(request, task):
    link = getattr(task, 'project_link', None)
    project = link.project if link else None
    return hooks.panel('Project', 'projects/_task_panel.html', {
        'task': task, 'project': project,
        'form': TaskProjectForm(task=task, initial={'project': project}),
        'can_change': project is None or can_edit(request.user, project),
    }, request, order=5)


def _project_table(request, title, projects, empty, order=100):
    return hooks.panel(title, 'projects/_project_table.html', {
        'projects': annotate(list(projects)), 'empty': empty,
    }, request, order=order)


@hooks.register('department_detail_panels')
def department_projects(request, department):
    projects = with_counts(Project.objects.filter(department=department))
    return _project_table(request, 'Projects', projects, 'No projects in this department.')


@hooks.register('user_detail_panels')
def user_projects(request, user):
    projects = with_counts(Project.objects.filter(Q(manager=user) | Q(members=user)).distinct())
    if not projects:
        return None
    return _project_table(request, 'Projects', projects, '')


@hooks.register('budget_detail_panels')
def budget_project(request, budget):
    project = project_of_budget(budget)
    if project is None:
        return None
    Spending().annotate([project.budget])
    return hooks.panel('Project', 'projects/_link_panel.html', {
        'project': project, 'own': project.budget_id == budget.pk,
        'what': 'This is the budget of' if project.budget_id == budget.pk else 'This budget is part of',
    }, request, order=5)


@hooks.register('order_detail_panels')
def order_project(request, order):
    project = project_of_budget(order.budget)
    if project is None:
        return None
    Spending().annotate([project.budget])
    return hooks.panel('Project', 'projects/_link_panel.html', {
        'project': project, 'what': 'This order is paid from the budget of',
    }, request, order=5)
