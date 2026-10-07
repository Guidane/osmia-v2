from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Work', {'fields': ('job_title', 'phone')}),
    )
    list_display = ('username', 'first_name', 'last_name', 'email', 'job_title', 'is_staff', 'is_active')
