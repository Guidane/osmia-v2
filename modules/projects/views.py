from collections import defaultdict
from decimal import Decimal

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView

from budgets.models import Spending, TaskBudget
from core import hooks
from departments.models import Department
from tasks.models import Task

from .forms import AddTaskForm, ProjectForm, TaskProjectForm
from .models import Project


def can_edit(user, project=None):
    """Who may change a project: people with the permission, and its manager."""
    if project is None:
        return user.has_perm('projects.add_project')
    return user.has_perm('projects.change_project') or (project.manager_id is not None and project.manager_id == user.pk)


def annotate(projects, spending=None):
    """Set budget figures (``amount``, ``spent``, ``remaining``, ``used_pct``,
    ``over``, via the budget) and task progress on each project."""
    spending = spending or Spending()
    spending.annotate([p.budget for p in projects if p.budget_id])
    for p in projects:
        p.tasks_total = getattr(p, 'n_tasks', 0)
        p.tasks_done = getattr(p, 'n_done', 0)
        p.progress = int(p.tasks_done / p.tasks_total * 100) if p.tasks_total else 0
    return projects


def with_counts(qs):
    return qs.select_related('department', 'manager', 'budget').annotate(
        n_tasks=Count('task_links', distinct=True),
        n_done=Count('task_links', filter=Q(task_links__task__status=Task.Status.DONE), distinct=True),
    )


class ProjectListView(LoginRequiredMixin, ListView):
    template_name = 'projects/project_list.html'

    def get_queryset(self):
        qs = with_counts(Project.objects.all())
        status = self.request.GET.get('status', 'open')
        if status == 'open':
            qs = qs.filter(status__in=Project.OPEN)
        elif status in Project.Status.values:
            qs = qs.filter(status=status)
        if self.request.GET.get('department'):
            qs = qs.filter(department_id=self.request.GET['department'])
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(description__icontains=q))
        return annotate(list(qs))

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            **kwargs, statuses=Project.Status.choices, status=self.request.GET.get('status', 'open'),
            departments=Department.objects.select_related('parent__parent'),
        )


def spending_by_source(project, spending):
    """{label: amount} of what the project's budgets were spent on, e.g. {'Materials': ..., 'Orders': ...}."""
    ids = set(project.budget_ids())
    totals = defaultdict(Decimal)
    for budget_id in ids:
        for label, amount in spending.by_source[budget_id].items():
            totals[label] += amount
    for task_id in TaskBudget.objects.filter(budget_id__in=ids).values_list('task_id', flat=True):
        for label, amount in spending.by_task[task_id].items():
            totals[label] += amount
    return dict(sorted(totals.items(), key=lambda i: -i[1]))


def project_orders(project):
    """Orders placed on the project's budget or its sub-budgets (when Orders is installed)."""
    if not apps.is_installed('orders') or not project.budget_id:
        return None
    Order = apps.get_model('orders', 'Order')
    return list(Order.objects.filter(budget_id__in=project.budget_ids()).select_related('supplier', 'budget').prefetch_related('lines'))


def project_materials(task_ids):
    """Stock issued to (and returned from) the project's tasks (when Stock is installed)."""
    if not apps.is_installed('stock'):
        return None
    StockMove = apps.get_model('stock', 'StockMove')
    moves = list(StockMove.objects.filter(task_id__in=task_ids).select_related('part', 'task', 'location')[:200])
    for m in moves:
        m.cost = -m.delta * m.unit_cost  # an issue (negative) costs, a return gives back
    return moves


class ProjectDetailView(LoginRequiredMixin, DetailView):
    model = Project

    def get_queryset(self):
        return with_counts(Project.objects.all()).select_related('budget__parent')

    def get_context_data(self, **kwargs):
        p = self.object
        spending = Spending()
        annotate([p], spending)
        tasks = list(Task.objects.filter(project_link__project=p).select_related('assignee', 'department', 'budget_link__budget'))
        for t in tasks:
            t.cost_total = spending.task_total(t.pk)
            t.off_budget = p.budget_id and getattr(getattr(t, 'budget_link', None), 'budget_id', None) not in p.budget_ids()
        orders = project_orders(p)
        materials = project_materials([t.pk for t in tasks])
        return super().get_context_data(
            **kwargs,
            tasks=tasks,
            by_source=spending_by_source(p, spending) if p.budget_id else {},
            orders=orders,
            orders_open=sum(1 for o in orders or [] if o.status in ('draft', 'placed', 'partial')),
            materials=materials,
            materials_cost=sum((m.cost for m in materials or []), Decimal(0)),
            add_form=AddTaskForm(project=p),
            can_edit=can_edit(self.request.user, p),
            panels=hooks.collect('project_detail_panels', self.request, p),
        )


@login_required
def project_form(request, pk=None):
    """Create or edit a project, and with it its budget."""
    project = get_object_or_404(Project, pk=pk) if pk else Project()
    if not can_edit(request.user, project if pk else None):
        raise PermissionDenied
    initial = {}
    if not pk:
        initial = {'manager': request.user, 'department': Department.objects.filter(pk=request.GET.get('department') or None).first()}
    form = ProjectForm(request.POST or None, instance=project, initial=initial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            if not pk:
                form.instance.created_by = request.user
            project = form.save()
            parent = form.cleaned_data['budget_parent']
            if project.budget_id and parent is None and project.budget.parent_id:
                project.budget.parent = None  # moved to the top
            project.set_budget(form.cleaned_data['budget_amount'], parent)
        messages.success(request, f'{project} saved.')
        return redirect(project)
    return render(request, 'projects/project_form.html', {
        'form': form, 'project': project,
        'heading': f'Edit {project}' if pk else 'New project',
        'cancel_url': project.get_absolute_url() if pk else None,
    })


@login_required
@require_POST
def add_task(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if not can_edit(request.user, project):
        raise PermissionDenied
    form = AddTaskForm(request.POST, project=project)
    if not form.is_valid():
        for error in form.non_field_errors() or ['That task can\'t be added.']:
            messages.error(request, error)
        return redirect(project)
    task = form.cleaned_data['task']
    with transaction.atomic():
        if task is None:
            task = Task.objects.create(
                title=form.cleaned_data['title'].strip(), assignee=form.cleaned_data['assignee'],
                due_date=form.cleaned_data['due_date'], created_by=request.user,
                department=project.department or getattr(getattr(form.cleaned_data['assignee'], 'department_membership', None), 'department', None),
            )
        project.add_task(task)
    messages.success(request, f'"{task}" is in {project}' + (', charged to its budget.' if project.budget_id else '.'))
    return redirect(project)


@login_required
@require_POST
def remove_task(request, pk, task_pk):
    project = get_object_or_404(Project, pk=pk)
    if not can_edit(request.user, project):
        raise PermissionDenied
    task = get_object_or_404(Task, pk=task_pk, project_link__project=project)
    project.remove_task(task)
    messages.success(request, f'"{task}" was taken out of {project}. The task itself is kept.')
    return redirect(project)


@login_required
@require_POST
def set_task_project(request, task_pk):
    """Put a task in a project, or take it out (from the task page's panel)."""
    task = get_object_or_404(Task, pk=task_pk)
    form = TaskProjectForm(request.POST, task=task)
    if not form.is_valid():
        messages.error(request, 'Pick one of the open projects.')
        return redirect(task)
    new = form.cleaned_data['project']
    link = getattr(task, 'project_link', None)
    old = link.project if link else None
    for p in {old, new} - {None}:
        if not can_edit(request.user, p):
            raise PermissionDenied
    if old == new:
        return redirect(task)
    with transaction.atomic():
        if old:
            old.remove_task(task)
        if new:
            new.add_task(task)
    messages.success(request, f'Task moved to {new}.' if new else f'Task taken out of {old}.')
    return redirect(task)
