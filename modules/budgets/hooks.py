from django.urls import reverse

from core import hooks

from .forms import TaskBudgetForm
from .models import Budget, Spending, TaskBudget


@hooks.register('dashboard_widgets')
def over_budget(request):
    budgets = Spending().annotate(list(Budget.objects.all()))
    count = sum(1 for b in budgets if b.over)
    return hooks.Widget('Budgets over their amount', count, reverse('budgets:list'), tone='warn' if count else 'ok')


@hooks.register('task_detail_panels')
def task_budget(request, task):
    link = TaskBudget.objects.filter(task=task).select_related('budget__department').first()
    budget = link.budget if link else None
    spending = Spending()
    if budget:
        spending.annotate([budget])
    return hooks.panel('Budget', 'budgets/_task_panel.html', {
        'task': task,
        'budget': budget,
        'task_cost': spending.task_total(task.pk),
        'mismatch': budget is not None and budget.department_id != task.department_id,
        'form': TaskBudgetForm(task=task, initial={'budget': budget}),
    }, request, order=6)


@hooks.register('department_detail_panels')
def department_budgets(request, department):
    budgets = Spending().annotate(list(department.budgets.select_related('parent__parent')))
    return hooks.panel('Budgets', 'budgets/_department_panel.html', {
        'budgets': budgets, 'department': department,
    }, request)
