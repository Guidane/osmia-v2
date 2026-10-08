from collections import defaultdict
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from core import hooks

from .forms import TaskForm, TaskGroupForm
from .models import Task, TaskGroup


def filter_tasks(request, qs):
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
    assignee = request.GET.get('assignee')
    if assignee == 'none':
        qs = qs.filter(assignee__isnull=True)
    elif assignee:
        qs = qs.filter(assignee_id=assignee)
    group = request.GET.get('group')
    if group == 'none':
        qs = qs.filter(group__isnull=True)
    elif group and group.isdigit():
        qs = qs.filter(group_id=group)
    return qs


def with_subtask_counts(qs):
    return qs.annotate(subtask_count=Count('subtasks', distinct=True),
                       subtasks_done=Count('subtasks', filter=Q(subtasks__status=Task.Status.DONE), distinct=True))


def task_tree(tasks):
    """The top-level tasks of ``tasks``, each with ``tree``: itself and the
    tasks below it, in tree order, for a tree table (core/tree_table.js).
    A task whose parent isn't in ``tasks`` (filtered out) is top-level here."""
    tasks = list(tasks)
    shown = {t.pk for t in tasks}
    children, roots = defaultdict(list), []
    for t in tasks:
        (children[t.parent_id] if t.parent_id in shown else roots).append(t)

    def walk(task, depth, seen):
        task.depth, task.tree_parent = depth, task.parent_id if depth else ''
        yield task
        for child in children[task.pk]:
            if child.pk not in seen:
                yield from walk(child, depth + 1, seen | {child.pk})

    for root in roots:
        root.tree = list(walk(root, 0, {root.pk}))
    return roots


def filter_status(qs, status):
    if status == 'open':
        return qs.exclude(status=Task.Status.DONE)
    if status in Task.Status.values:
        return qs.filter(status=status)
    return qs


def filter_context(request):
    return {
        'people': get_user_model().objects.filter(is_active=True),
        'selected_assignee': request.GET.get('assignee', ''),
        'groups': TaskGroup.objects.all(),
        'selected_group': request.GET.get('group', ''),
    }


@login_required
def board(request):
    tasks = filter_tasks(request, Task.objects.select_related('assignee', 'group'))
    columns = [
        {'status': value, 'label': label, 'tasks': [t for t in tasks if t.status == value]}
        for value, label in Task.Status.choices
    ]
    return render(request, 'tasks/board.html', {'columns': columns, 'statuses': Task.Status.choices, **filter_context(request)})


GANTT_WEEKS = (2, 4, 6, 8, 12)


@login_required
def gantt(request):
    today = timezone.localdate()
    weeks = int(request.GET['weeks']) if request.GET.get('weeks', '').isdigit() else 6
    if weeks not in GANTT_WEEKS:
        weeks = 6
    try:
        window_start = date.fromisoformat(request.GET['start'])
    except (KeyError, ValueError):
        window_start = today - timedelta(days=7)
    window_start -= timedelta(days=window_start.weekday())  # always start on a Monday
    days = weeks * 7
    window_end = window_start + timedelta(days=days - 1)

    def pct(n):
        return f'{n / days * 100:.4f}'

    status = request.GET.get('status', 'all')
    qs = filter_status(filter_tasks(request, Task.objects.select_related('assignee', 'group')), status)
    rows, unscheduled = [], 0
    for task in qs:
        if not task.start_date and not task.due_date:
            unscheduled += 1
            continue
        # Without a start date, the bar runs from the day the task was created.
        end = task.due_date or task.start_date
        start = task.start_date or min(timezone.localdate(task.created_at), end)
        if end < window_start or start > window_end:
            continue
        shown_start, shown_end = max(start, window_start), min(end, window_end)
        rows.append({
            'task': task,
            'start': start,
            'end': end,
            'estimated_start': task.start_date is None,
            'left': pct((shown_start - window_start).days),
            'width': pct((shown_end - shown_start).days + 1),
            'clipped_start': start < window_start,
            'clipped_end': end > window_end,
        })
    rows.sort(key=lambda r: (r['start'], r['end']))
    sections = gantt_sections(rows, pct, window_start)

    return render(request, 'tasks/gantt.html', {
        'rows': rows,
        'sections': sections,
        'unscheduled': unscheduled,
        'weeks': [
            {'monday': window_start + timedelta(weeks=i), 'left': pct(i * 7)} for i in range(weeks)
        ],
        'hidden_params': [('start', window_start.isoformat()), ('weeks', weeks)],
        'today_left': pct((today - window_start).days + 0.5) if window_start <= today <= window_end else None,
        'window_start': window_start,
        'window_end': window_end,
        'prev_start': (window_start - timedelta(weeks=max(1, weeks // 2))).isoformat(),
        'next_start': (window_start + timedelta(weeks=max(1, weeks // 2))).isoformat(),
        'week_choices': GANTT_WEEKS,
        'selected_weeks': weeks,
        'statuses': Task.Status.choices,
        'selected_status': status,
        **filter_context(request),
    })


def gantt_sections(rows, pct, window_start):
    """The Gantt rows by group (groups by name, then tasks without one), each
    with a summary bar from its first start to its last end."""
    by_group = defaultdict(list)
    for r in rows:
        by_group[r['task'].group].append(r)
    groups = sorted((g for g in by_group if g is not None), key=lambda g: g.name.lower())
    if None in by_group:
        groups.append(None)
    sections = []
    for group in groups:
        items = by_group[group]
        start, end = min(r['start'] for r in items), max(r['end'] for r in items)
        left = min(float(r['left']) for r in items)
        right = max(float(r['left']) + float(r['width']) for r in items)
        sections.append({
            'group': group, 'rows': items, 'start': start, 'end': end,
            'left': f'{left:.4f}', 'width': f'{right - left:.4f}',
            'done': sum(1 for r in items if r['task'].status == Task.Status.DONE),
        })
    return sections


class TaskListView(LoginRequiredMixin, ListView):
    """Tasks as a tree table: subtasks under their parent. Pages count top-level tasks."""
    model = Task
    template_name = 'tasks/task_list.html'  # the queryset becomes a list of top-level tasks
    paginate_by = 50
    mine = False

    def get_queryset(self):
        qs = filter_tasks(self.request, with_subtask_counts(Task.objects.select_related('assignee', 'parent', 'group')))
        if self.mine:
            qs = qs.filter(assignee=self.request.user)
        return task_tree(filter_status(qs, self.request.GET.get('status', 'open')))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(
            **kwargs, **filter_context(self.request), mine=self.mine,
            statuses=Task.Status.choices, selected_status=self.request.GET.get('status', 'open'),
        )
        ctx['rows'] = [row for root in ctx['object_list'] for row in root.tree]
        ctx['tree'] = 'tasks-mine' if self.mine else 'tasks'  # remembers which tasks are open
        query = self.request.GET.copy()
        query.pop('page', None)
        ctx['page_query'] = query.urlencode() + '&' if query else ''
        return ctx


class TaskDetailView(LoginRequiredMixin, DetailView):
    model = Task

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['panels'] = hooks.collect('task_detail_panels', self.request, self.object)
        below = with_subtask_counts(Task.objects.filter(pk__in=self.object.descendant_ids()).select_related('assignee'))
        ctx['subtasks'] = [row for root in task_tree(below) for row in root.tree]
        ctx['statuses'] = Task.Status.choices
        return ctx


class TaskCreateView(LoginRequiredMixin, CreateView):
    model = Task
    form_class = TaskForm
    template_name = 'core/form.html'
    extra_context = {'heading': 'New task'}

    def get_initial(self):
        initial = {'assignee': self.request.GET.get('assignee') or self.request.user.pk}
        if self.request.GET.get('status') in Task.Status.values:
            initial['status'] = self.request.GET['status']
        if str(self.request.GET.get('parent', '')).isdigit():
            initial['parent'] = self.request.GET['parent']
            parent = Task.objects.filter(pk=initial['parent']).first()
            if parent and parent.group_id:
                initial['group'] = parent.group_id
        if str(self.request.GET.get('group', '')).isdigit():
            initial['group'] = self.request.GET['group']
        return initial

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, 'Task created.')
        return super().form_valid(form)


class TaskUpdateView(LoginRequiredMixin, UpdateView):
    model = Task
    form_class = TaskForm
    template_name = 'core/form.html'

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, heading='Edit task', cancel_url=self.object.get_absolute_url())


class TaskDeleteView(LoginRequiredMixin, DeleteView):
    model = Task
    template_name = 'core/confirm_delete.html'
    success_url = reverse_lazy('tasks:board')


@login_required
@require_POST
def set_status(request, pk):
    task = get_object_or_404(Task, pk=pk)
    status = request.POST.get('status')
    if status in Task.Status.values:
        task.status = status
        task.save()
    next_url = request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect(task)


def safe_next(request, fallback):
    next_url = request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect(fallback)


@login_required
@require_POST
def move_to_group(request):
    """Put the ticked tasks (and their subtasks) in a group, or in none."""
    group_id = request.POST.get('group', '')
    group = get_object_or_404(TaskGroup, pk=group_id) if group_id.isdigit() else None
    tasks = Task.objects.filter(pk__in=[i for i in request.POST.getlist('tasks') if i.isdigit()])
    moved = 0
    for task in tasks:
        task.move_to_group(group)
        moved += 1
    if moved:
        where = f'to {group}' if group else 'out of their group'
        messages.success(request, f'Moved {moved} task{"s" if moved != 1 else ""} {where}, with their subtasks.')
    return safe_next(request, 'tasks:list')


# -- Task groups ------------------------------------------------------------------------

class GroupListView(LoginRequiredMixin, ListView):
    model = TaskGroup
    template_name = 'tasks/group_list.html'

    def get_queryset(self):
        return TaskGroup.objects.annotate(
            task_count=Count('tasks'),
            open_count=Count('tasks', filter=~Q(tasks__status=Task.Status.DONE)),
        )

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, ungrouped=Task.objects.filter(group=None).count())


class GroupFormMixin(LoginRequiredMixin):
    model = TaskGroup
    form_class = TaskGroupForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('tasks:groups')

    def form_valid(self, form):
        messages.success(self.request, 'Group saved.')
        return super().form_valid(form)


class GroupCreateView(GroupFormMixin, CreateView):
    extra_context = {'heading': 'New task group'}


class GroupUpdateView(GroupFormMixin, UpdateView):
    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, heading=f'Edit {self.object}', cancel_url=reverse_lazy('tasks:groups'))


class GroupDeleteView(LoginRequiredMixin, DeleteView):
    """Deleting a group keeps its tasks; they just have no group."""
    model = TaskGroup
    template_name = 'core/confirm_delete.html'
    success_url = reverse_lazy('tasks:groups')
