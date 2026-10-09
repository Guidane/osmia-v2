from django.contrib import admin

from .models import Project, ProjectTask


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'status', 'department', 'manager', 'budget')
    list_filter = ('status', 'department')
    search_fields = ('code', 'name')


admin.site.register(ProjectTask)
