from django import forms
from django.contrib.auth import get_user_model

from .models import Tool


class ToolForm(forms.ModelForm):
    class Meta:
        model = Tool
        fields = ['name', 'part_number', 'kind', 'manufacturer', 'asset_tag', 'storage', 'responsible',
                  'calibration_due', 'notes', 'is_active']
        widgets = {'calibration_due': forms.DateInput(attrs={'type': 'date'}), 'notes': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['responsible'].queryset = get_user_model().objects.filter(is_active=True)
