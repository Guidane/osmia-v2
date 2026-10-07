from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from core import hooks

from .forms import TaskForm
from .models import Task


def filter_tasks(request, qs):
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
    assignee = request.GET.get('assignee')
    if assignee == 'none':
        qs = qs.filter(assignee__isnull=True)
    elif assignee:
        qs = qs.filter(assignee_id=assignee)
    return qs


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
    }


@login_required
def board(request):
    tasks = filter_tasks(request, Task.objects.select_related('assignee'))
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
    qs = filter_status(filter_tasks(request, Task.objects.select_related('assignee')), status)
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

    return render(request, 'tasks/gantt.html', {
        'rows': rows,
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


class TaskListView(LoginRequiredMixin, ListView):
    model = Task
    paginate_by = 50
    mine = False

    def get_queryset(self):
        qs = filter_tasks(self.request, Task.objects.select_related('assignee'))
        if self.mine:
            qs = qs.filter(assignee=self.request.user)
        return filter_status(qs, self.request.GET.get('status', 'open'))

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            **kwargs, **filter_context(self.request), mine=self.mine,
            statuses=Task.Status.choices, selected_status=self.request.GET.get('status', 'open'),
        )


class TaskDetailView(LoginRequiredMixin, DetailView):
    model = Task

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['panels'] = hooks.collect('task_detail_panels', self.request, self.object)
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
