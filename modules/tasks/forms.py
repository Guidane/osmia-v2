from django import forms
from django.contrib.auth import get_user_model

from departments.models import Department, department_of

from .models import Task


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ['title', 'description', 'status', 'priority', 'assignee', 'department', 'start_date', 'due_date']
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

    def clean(self):
        data = super().clean()
        start, due = data.get('start_date'), data.get('due_date')
        if start and due and start > due:
            self.add_error('due_date', 'Due date cannot be before the start date.')
        if not data.get('department') and data.get('assignee'):
            data['department'] = department_of(data['assignee'])
        return data
