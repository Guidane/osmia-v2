from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from budgets.models import Budget
from core.trees import get_or_create_path
from departments.models import Department
from tasks.models import Task

from .models import Project

# (name, status, department, manager, members, paid from budget, amount, starts/ends in N days, task title prefixes, description)
PROJECTS = [
    ('Aisle 3 relabel and cable racks', Project.Status.ACTIVE, 'Operations > Warehouse', 'alice', ['bob'], 'B-OPS', 15_000,
     (-8, 20), ['Label the bins in aisle 3', 'Move the cable stock to the new racks'],
     'New bin labels for aisle 3, and the cable reels moved to the new racks.'),
    ('PDU-100 production run', Project.Status.ACTIVE, 'Engineering', 'alice', ['carla', 'bob'], 'B-ENG', 40_000,
     (-14, 30), ['Build PDU-100'], 'The first batch of PDU-100 power distribution units.'),
    ('Automatic test bench', Project.Status.PLANNED, 'Engineering > QA', 'carla', [], 'B-ENG-QA', 8_000,
     (30, 90), [], 'A bench that runs the PDU-100 final test by itself.'),
]


def with_subtasks(tasks):
    found, todo = [], list(tasks)
    while todo:
        task = todo.pop()
        found.append(task)
        todo.extend(task.subtasks.all())
    return found


def load():
    if Project.objects.exists():
        return
    users = {u.username: u for u in get_user_model().objects.all()}
    today = timezone.localdate()
    for name, status, dept, manager, members, parent, amount, (start, end), prefixes, description in PROJECTS:
        project = Project.objects.create(
            name=name, status=status, description=description, department=get_or_create_path(Department, dept),
            manager=users.get(manager), created_by=users.get('admin'),
            start_date=today + timedelta(days=start), end_date=today + timedelta(days=end),
        )
        project.members.set([users[m] for m in members if m in users])
        project.set_budget(Decimal(amount), Budget.objects.filter(budget_number=parent).first())
        for prefix in prefixes:
            for task in with_subtasks(Task.objects.filter(title__startswith=prefix, parent=None)):
                project.add_task(task)
