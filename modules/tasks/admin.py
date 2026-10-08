from django.contrib import admin

from .models import Task, TaskGroup


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'group', 'status', 'priority', 'assignee', 'due_date')
    list_filter = ('group', 'status', 'priority', 'assignee')
    search_fields = ('title', 'description')


@admin.register(TaskGroup)
class TaskGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'color', 'description')
