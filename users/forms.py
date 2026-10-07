from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User

PROFILE_FIELDS = ['first_name', 'last_name', 'email', 'job_title', 'phone']


class UserCreateForm(UserCreationForm):
    class Meta:
        model = User
        fields = ['username', *PROFILE_FIELDS, 'is_staff']


class UserEditForm(forms.ModelForm):
    """For managers editing any user."""

    class Meta:
        model = User
        fields = ['username', *PROFILE_FIELDS, 'is_staff', 'is_active']


class ProfileForm(forms.ModelForm):
    """For users editing their own profile."""

    class Meta:
        model = User
        fields = PROFILE_FIELDS
