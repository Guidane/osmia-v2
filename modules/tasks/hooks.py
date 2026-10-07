from django.urls import reverse
from django.utils import timezone

from core import hooks

from .models import Task


@hooks.register('dashboard_widgets')
def my_open_tasks(request):
    count = Task.objects.filter(assignee=request.user).exclude(status=Task.Status.DONE).count()
    return hooks.Widget('My open tasks', count, reverse('tasks:mine'))


@hooks.register('dashboard_widgets')
def overdue_tasks(request):
    count = Task.objects.exclude(status=Task.Status.DONE).filter(due_date__lt=timezone.localdate()).count()
    return hooks.Widget('Overdue tasks', count, reverse('tasks:list'), tone='warn' if count else 'ok')


@hooks.register('user_detail_panels')
def user_tasks(request, user):
    tasks = user.assigned_tasks.exclude(status=Task.Status.DONE).select_related('assignee')
    return hooks.panel(
        'Open tasks', 'tasks/_user_panel.html', {'tasks': tasks, 'person': user}, request, order=10,
    )


# -- Automations: triggers, the "Create tasks" action, and task groups ------------------

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from django.dispatch import receiver

from core import automation
from core.automation import Field, Param
from departments.models import department_of

from .models import TaskBatch

TASK_FIELDS = [
    Field('title', 'Title', lambda t: t.title),
    Field('status', 'Status', lambda t: t.get_status_display()),
    Field('priority', 'Priority', lambda t: t.get_priority_display()),
    Field('department', 'Department', lambda t: t.department or ''),
    Field('assignee', 'Assignee', lambda t: t.assignee or ''),
]
automation.event('tasks.task_created', 'A task is created', fields=TASK_FIELDS)
automation.event('tasks.task_completed', 'A task is completed', fields=TASK_FIELDS)
automation.event('tasks.task_status_changed', "A task's status changes", fields=TASK_FIELDS)
automation.event('tasks.batch_completed', 'All tasks created by a rule are done', fields=[
    Field('rule', 'Created by rule', lambda b: b.rule_id or '', kind='rule'),
    Field('source', 'For', lambda b: b.source_label),
])
automation.status_model(Task)
automation.track(Task, 'status')


def _owner(record):
    """The person a record is about: its assignee, owner or creator."""
    for name in ('assignee', 'responsible', 'created_by'):
        person = getattr(record, name, None)
        if person is not None:
            return person
    return None


@automation.action('tasks.create_tasks', 'Create tasks', params=[
    Param('tasks', 'Tasks', 'task_list', required=True, choices=tuple(Task.Priority.choices),
          help='Use {object} or {source} in a title to put in the name of the record, e.g. "Check {source} on arrival".'),
])
def create_tasks(ctx, params):
    templates = [t for t in (params.get('tasks') or []) if (t.get('title') or '').strip()]
    if not templates:
        raise ValidationError('Add at least one task with a title.')
    source = ctx.get('source') or ctx.get('object')
    rule = ctx.get('rule')
    batch = TaskBatch.objects.create(
        name=getattr(rule, 'name', 'Automation')[:200], rule_id=getattr(rule, 'pk', None),
        source_type=ContentType.objects.get_for_model(source) if source is not None else None,
        source_id=getattr(source, 'pk', None), source_label=str(source)[:200] if source is not None else '',
    )
    users = get_user_model().objects
    created = []
    for t in templates:
        who = t.get('assignee') or ''
        assignee = _owner(ctx.get('object')) if who == '@owner' else users.filter(pk=who).first() if str(who).isdigit() else None
        days = t.get('due_in_days')
        task = Task.objects.create(
            title=automation.render(t['title'], ctx)[:200],
            description=automation.render(t.get('description') or '', ctx),
            priority=int(t['priority']) if str(t.get('priority', '')).isdigit() else Task.Priority.NORMAL,
            assignee=assignee,
            department_id=int(t['department']) if str(t.get('department') or '').isdigit() else getattr(department_of(assignee), 'pk', None),
            due_date=timezone.localdate() + timedelta(days=int(days)) if str(days or '').lstrip('-').isdigit() else None,
            batch=batch,
        )
        created.append(task.title)
    return f'Created {len(created)} task{"s" if len(created) != 1 else ""}: ' + '; '.join(created)


@receiver(post_save, sender=Task, dispatch_uid='tasks-automation-events')
def task_saved(sender, instance, created, **kwargs):
    if created:
        automation.remember_saved(instance, 'status')
        automation.emit('tasks.task_created', instance)
        return
    change = automation.changed(instance, 'status')
    automation.remember_saved(instance, 'status')
    if not change:
        return
    automation.emit('tasks.task_status_changed', instance)
    if instance.status == Task.Status.DONE:
        automation.emit('tasks.task_completed', instance)
        batch = instance.batch
        if batch and batch.completed_at is None and batch.is_complete():
            batch.completed_at = timezone.now()
            batch.save(update_fields=['completed_at'])
            automation.emit('tasks.batch_completed', batch, source=batch.source)


def source_tasks_panel(request, record):
    """Tasks that automation rules created for ``record`` (an order, an assembly, ...)."""
    batches = TaskBatch.objects.filter(source_type=ContentType.objects.get_for_model(record), source_id=record.pk)
    tasks = Task.objects.filter(batch__in=batches).select_related('assignee', 'batch')
    if not tasks:
        return None
    return hooks.panel('Tasks from automations', 'tasks/_batch_panel.html', {'tasks': tasks}, request, order=20)


hooks.register('order_detail_panels')(source_tasks_panel)
hooks.register('assembly_detail_panels')(source_tasks_panel)
