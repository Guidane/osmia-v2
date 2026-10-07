from collections import defaultdict
from decimal import Decimal

from django.db import models
from django.urls import reverse

from core import hooks
from core.trees import TreeNode


class Budget(TreeNode):
    """Money set aside, nested, e.g. ``FY2026 > Operations > Warehouse Ops``.

    Tasks are charged to a budget of their own department; what those tasks
    cost (materials issued to them, ...) counts as spent.
    """

    budget_number = models.CharField(max_length=50, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    department = models.ForeignKey(
        'departments.Department', null=True, blank=True, on_delete=models.SET_NULL, related_name='budgets',
    )

    def get_absolute_url(self):
        return reverse('budgets:detail', args=[self.pk])

    def __str__(self):
        return f'{self.budget_number} · {self.full_path()}' if self.budget_number else self.full_path()


class TaskBudget(models.Model):
    """Charges a task to a budget. Kept in this module so Tasks doesn't depend on Budgets."""

    task = models.OneToOneField('tasks.Task', on_delete=models.CASCADE, related_name='budget_link')
    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name='task_links')

    def __str__(self):
        return f'{self.task} → {self.budget}'


class Spending:
    """Spend per budget, gathered from every module's ``task_costs`` hook
    (what tasks charged to a budget cost) and ``budget_costs`` hook (spending
    booked straight on a budget, e.g. placed orders).

    ``own[budget_id]``   what was spent directly on the budget
    ``total[budget_id]`` own spend plus every sub-budget's
    ``by_task[task_id]`` {cost label: amount}, e.g. {'Materials': 89.00}
    """

    def __init__(self):
        task_budget = dict(TaskBudget.objects.values_list('task_id', 'budget_id'))
        self.by_task = defaultdict(lambda: defaultdict(Decimal))
        for costs in hooks.collect('task_costs', list(task_budget)):
            for task_id, amount in costs.by_task.items():
                self.by_task[task_id][costs.label] += amount

        self.own = defaultdict(Decimal)
        for task_id, budget_id in task_budget.items():
            self.own[budget_id] += sum(self.by_task[task_id].values(), Decimal(0))
        self.by_source = defaultdict(lambda: defaultdict(Decimal))  # {budget id: {label: amount}}
        budget_ids = list(Budget.objects.values_list('pk', flat=True))
        for costs in hooks.collect('budget_costs', budget_ids):
            for budget_id, amount in costs.by_task.items():  # Costs.by_task holds budget ids here
                self.own[budget_id] += amount
                self.by_source[budget_id][costs.label] += amount

        parents = dict(Budget.objects.values_list('pk', 'parent_id'))
        self.total = defaultdict(Decimal)
        for budget_id, amount in self.own.items():
            node, seen = budget_id, set()
            while node is not None and node not in seen:
                seen.add(node)
                self.total[node] += amount
                node = parents.get(node)

    def task_total(self, task_id):
        return sum(self.by_task[task_id].values(), Decimal(0))

    def annotate(self, budgets):
        """Set ``spent``, ``remaining`` and ``used_pct`` on each budget."""
        for b in budgets:
            b.spent = self.total[b.pk]
            b.remaining = b.amount - b.spent
            b.used_pct = min(int(b.spent / b.amount * 100), 100) if b.amount else (100 if b.spent else 0)
            b.over = b.spent > b.amount
        return budgets
