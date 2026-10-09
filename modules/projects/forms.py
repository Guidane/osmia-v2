from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model

from budgets.models import Budget
from departments.models import Department
from tasks.models import Task

from .models import Project


class ProjectForm(forms.ModelForm):
    """A project and its budget: how much, and under which budget it sits."""

    budget_amount = forms.DecimalField(label='Budget', max_digits=14, decimal_places=2, min_value=0, initial=Decimal(0),
                                       help_text='The money set aside for the project.')
    budget_parent = forms.ModelChoiceField(Budget.objects.none(), required=False, label='Paid from',
                                           empty_label='(a budget of its own, at the top)',
                                           help_text='The budget the project\'s budget sits under, e.g. FY2026 > Engineering.')

    class Meta:
        model = Project
        fields = ['name', 'code', 'status', 'department', 'manager', 'members', 'start_date', 'end_date', 'description']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'members': forms.SelectMultiple(attrs={'size': 6}),
            'description': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        users = get_user_model().objects.filter(is_active=True)
        self.fields['manager'].queryset = users
        self.fields['members'].queryset = users
        self.fields['members'].help_text = 'Ctrl+click to pick several.'
        self.fields['department'].queryset = Department.objects.select_related('parent__parent')
        own = self.instance.budget
        below = {own.pk, *own.descendant_ids()} if own else set()
        self.fields['budget_parent'].queryset = Budget.objects.exclude(pk__in=below).select_related('parent__parent')
        if own:
            self.fields['budget_amount'].initial = own.amount
            self.fields['budget_parent'].initial = own.parent_id

    def clean(self):
        data = super().clean()
        start, end = data.get('start_date'), data.get('end_date')
        if start and end and end < start:
            self.add_error('end_date', 'The end date is before the start date.')
        return data


class AddTaskForm(forms.Form):
    """Put an existing task in the project, or make a new one in it."""

    task = forms.ModelChoiceField(Task.objects.none(), required=False, empty_label='(pick a task)')
    title = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={'placeholder': 'Or a new task…'}))
    assignee = forms.ModelChoiceField(get_user_model().objects.none(), required=False, empty_label='(no one)')
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['task'].queryset = (Task.objects.exclude(project_link__project=project)
                                        .exclude(status=Task.Status.DONE).order_by('title'))
        self.fields['assignee'].queryset = get_user_model().objects.filter(is_active=True)

    def clean(self):
        data = super().clean()
        if not data.get('task') and not (data.get('title') or '').strip():
            raise forms.ValidationError('Pick a task, or give a new one a title.')
        return data


class TaskProjectForm(forms.Form):
    """The project a task belongs to (on the task's page)."""

    project = forms.ModelChoiceField(Project.objects.none(), required=False, empty_label='(none)')

    def __init__(self, *args, task=None, **kwargs):
        super().__init__(*args, **kwargs)
        current = getattr(getattr(task, 'project_link', None), 'project_id', None)
        self.fields['project'].queryset = Project.objects.filter(status__in=Project.OPEN) | Project.objects.filter(pk=current)
