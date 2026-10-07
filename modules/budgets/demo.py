from decimal import Decimal

from core.trees import get_or_create_path
from tasks.models import Task
from departments.models import Department

from .models import Budget, TaskBudget

# (path, number, amount, department): the Waggle V3 budget tree, plus Field Service.
BUDGETS = [
    ('FY2026', 'B-2026', 1_000_000, None),
    ('FY2026 > Operations', 'B-OPS', 400_000, 'Operations'),
    ('FY2026 > Operations > Warehouse Ops', 'B-OPS-WH', 250_000, 'Operations > Warehouse'),
    ('FY2026 > Operations > Logistics', 'B-OPS-LOG', 100_000, 'Operations > Logistics'),
    ('FY2026 > Operations > Field Service', 'B-OPS-FS', 50_000, 'Operations > Field Service'),
    ('FY2026 > Engineering', 'B-ENG', 500_000, 'Engineering'),
    ('FY2026 > Engineering > R&D', 'B-ENG-RND', 300_000, 'Engineering > R&D'),
    ('FY2026 > Engineering > QA & Test', 'B-ENG-QA', 120_000, 'Engineering > QA'),
    ('FY2026 > Marketing', 'B-MKT', 100_000, 'Marketing'),
]


def load():
    if Budget.objects.exists():
        return
    for path, number, amount, dept in BUDGETS:
        department = get_or_create_path(Department, dept) if dept else None
        get_or_create_path(Budget, path, budget_number=number, amount=Decimal(amount), department=department)

    # Charge each demo task with a department to that department's budget.
    for task in Task.objects.filter(budget_link=None, department__isnull=False):
        budget = Budget.objects.filter(department=task.department).first()
        if budget:
            TaskBudget.objects.create(task=task, budget=budget)
