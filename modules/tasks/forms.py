from django import forms
from django.contrib.auth import get_user_model

from departments.models import Department, department_of

from .models import Task, TaskGroup


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ['title', 'group', 'parent', 'description', 'status', 'priority', 'assignee', 'department', 'start_date', 'due_date']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 5}),
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'due_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assignee'].queryset = get_user_model().objects.filter(is_active=True)
        self.fields['department'].queryset = Department.objects.select_related('parent__parent')
        self.fields['department'].help_text = "Leave blank to use the assignee's department."
        parents = Task.objects.order_by('title')
        if self.instance.pk:
            parents = parents.exclude(pk__in={self.instance.pk, *self.instance.descendant_ids()})
        self.fields['parent'].queryset = parents
        self.fields['group'].empty_label = 'No group'
        self._editing, self._old_group_id = bool(self.instance.pk), self.instance.group_id

    def clean(self):
        data = super().clean()
        start, due = data.get('start_date'), data.get('due_date')
        if start and due and start > due:
            self.add_error('due_date', 'Due date cannot be before the start date.')
        if not self.instance.pk and not data.get('group') and data.get('parent') and 'group' not in self.data:
            data['group'] = data['parent'].group  # a new subtask joins its parent's group
        if not data.get('department') and data.get('assignee'):
            data['department'] = department_of(data['assignee'])
        return data

    def save(self, commit=True):
        task = super().save(commit=commit)
        if commit and self._editing and task.group_id != self._old_group_id:
            task.move_to_group(task.group)  # the subtasks move with it
        return task


class TaskGroupForm(forms.ModelForm):
    class Meta:
        model = TaskGroup
        fields = ['name', 'color', 'description']
