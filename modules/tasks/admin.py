from django.contrib import admin

from .models import Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'priority', 'assignee', 'due_date')
    list_filter = ('status', 'priority', 'assignee')
    search_fields = ('title', 'description')
