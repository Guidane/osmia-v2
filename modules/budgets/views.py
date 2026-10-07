from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView

from core import hooks
from core.trees import link_parents, sorted_by_path
from tasks.models import Task

from .forms import BudgetForm, SubBudgetFormSet, TaskBudgetForm
from .models import Budget, Spending, TaskBudget


def tree_rows(budgets):
    """Budgets in tree order for a tree table. ``depth`` counts only the
    ancestors that are in ``budgets`` too, so a filtered list or a subtree
    starts at the left; rows whose parent isn't shown get no ``tree_parent``."""
    rows = sorted_by_path(budgets)
    shown = {b.pk for b in rows}
    for b in rows:
        b.depth = sum(1 for a in b.ancestors()[:-1] if a.pk in shown)
        b.tree_parent = b.parent_id if b.parent_id in shown else ''
    return rows


def all_budgets():
    return link_parents(Budget.objects.select_related('department__parent'))


class BudgetListView(LoginRequiredMixin, ListView):
    model = Budget
    template_name = 'budgets/budget_list.html'  # the queryset becomes a sorted list

    def get_queryset(self):
        budgets = all_budgets()
        department = self.request.GET.get('department')
        if department:
            budgets = [b for b in budgets if str(b.department_id) == department]
        return Spending().annotate(tree_rows(budgets))


class BudgetDetailView(LoginRequiredMixin, DetailView):
    model = Budget

    def get_context_data(self, **kwargs):
        b = self.object
        spending = Spending()
        below = b.descendant_ids()
        subtree = spending.annotate(tree_rows([n for n in all_budgets() if n.pk in below]))
        allocated = sum((n.amount for n in subtree if n.parent_id == b.pk), Decimal(0))
        tasks = list(Task.objects.filter(budget_link__budget=b).select_related('assignee'))
        for t in tasks:
            t.costs = dict(spending.by_task[t.pk])
            t.cost_total = spending.task_total(t.pk)
        spending.annotate([b])
        return super().get_context_data(
            **kwargs,
            subtree=subtree,
            tree_key=f'budget-{b.pk}',
            allocated=allocated,
            over_allocated=allocated > b.amount,
            tasks=tasks,
            own_spent=spending.own[b.pk],
            panels=hooks.collect('budget_detail_panels', self.request, b),
        )


@login_required
def budget_form(request, pk=None):
    """Create or edit a budget, with its direct sub-budgets in a table below."""
    budget = get_object_or_404(Budget, pk=pk) if pk else Budget()
    if not request.user.has_perm('budgets.change_budget' if pk else 'budgets.add_budget'):
        raise PermissionDenied
    initial = {}
    if not pk:
        parent = Budget.objects.filter(pk=request.GET.get('parent') or None).first()
        initial = {'parent': parent, 'department': parent.department if parent else None}
    form = BudgetForm(request.POST or None, instance=budget, initial=initial)
    formset = SubBudgetFormSet(request.POST or None, instance=budget, prefix='subs',
                               queryset=Budget.objects.order_by('name'))
    can_add_subs, can_delete_subs = request.user.has_perm('budgets.add_budget'), request.user.has_perm('budgets.delete_budget')
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        new_subs = [f for f in formset.extra_forms if f.has_changed() and not f.cleaned_data.get('DELETE')]
        if (new_subs and not can_add_subs) or (formset.deleted_forms and not can_delete_subs):
            raise PermissionDenied
        with transaction.atomic():
            budget = form.save()
            formset.instance = budget
            for sub in formset.save(commit=False):
                if sub.department_id is None:
                    sub.department = budget.department  # like "Add sub-budget": the parent's department
                sub.save()
            for sub in formset.deleted_objects:
                sub.delete()
        messages.success(request, 'Budget saved.')
        return redirect(budget)
    return render(request, 'budgets/budget_form.html', {
        'form': form, 'formset': formset, 'budget': budget, 'can_add_subs': can_add_subs,
        'can_delete_subs': can_delete_subs,
        'heading': f'Edit {budget}' if pk else 'New budget',
        'cancel_url': budget.get_absolute_url() if pk else None,
    })


@login_required
@require_POST
def link_task(request, task_pk):
    """Set or clear the budget a task is charged to (from the task page's panel)."""
    task = get_object_or_404(Task, pk=task_pk)
    form = TaskBudgetForm(request.POST, task=task)
    if not form.is_valid():
        messages.error(request, "That budget doesn't belong to the task's department.")
    elif form.cleaned_data['budget']:
        TaskBudget.objects.update_or_create(task=task, defaults={'budget': form.cleaned_data['budget']})
        messages.success(request, f"Task charged to {form.cleaned_data['budget']}.")
    else:
        TaskBudget.objects.filter(task=task).delete()
    return redirect(task)
