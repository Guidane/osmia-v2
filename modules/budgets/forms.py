from django import forms

from core.trees import TreeNodeForm
from departments.models import Department

from .models import Budget


class BudgetForm(TreeNodeForm):
    class Meta:
        model = Budget
        fields = ['name', 'budget_number', 'amount', 'parent', 'department']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['department'].queryset = Department.objects.select_related('parent__parent')
        self.fields['parent'].label = 'Parent budget'


class SubBudgetForm(forms.ModelForm):
    class Meta:
        model = Budget
        fields = ['name', 'budget_number', 'amount', 'department']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['department'].queryset = Department.objects.select_related('parent__parent')
        self.fields['name'].required = False  # blank extra rows are skipped; see has_changed()

    def has_changed(self):
        # An extra row only counts once it has a name (amount defaults to 0).
        return bool(self.instance.pk) or bool(self.data.get(self.add_prefix('name'), '').strip())

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if not name and self.has_changed():
            raise forms.ValidationError('Give the sub-budget a name.')
        return name


class BaseSubBudgetFormSet(forms.BaseInlineFormSet):
    """A budget's direct sub-budgets, edited on its own form.

    A sub-budget can only be deleted while nothing hangs off it: no
    sub-budgets of its own and no tasks or orders charged to it.
    """

    def clean(self):
        super().clean()
        for form in self.deleted_forms:
            b = form.instance
            if not b.pk:
                continue
            if b.children.exists():
                raise forms.ValidationError(f'{b.name} has sub-budgets of its own; delete or move those first.')
            if b.task_links.exists() or (hasattr(b, 'orders') and b.orders.exists()):
                raise forms.ValidationError(f"{b.name} has tasks or orders charged to it, so it can't be deleted.")


SubBudgetFormSet = forms.inlineformset_factory(
    Budget, Budget, form=SubBudgetForm, formset=BaseSubBudgetFormSet, fk_name='parent', extra=2, can_delete=True,
)


class TaskBudgetForm(forms.Form):
    """A task's budget must belong to the task's department (as in Waggle V3)."""

    budget = forms.ModelChoiceField(Budget.objects.none(), required=False, empty_label='(none)')

    def __init__(self, *args, task, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Budget.objects.filter(department=task.department) if task.department_id else Budget.objects.none()
        self.fields['budget'].queryset = qs.select_related('parent__parent')
