from django.contrib import admin

from .models import LogEntry


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'module', 'object_label', 'action', 'user', 'actor_module', 'chain_id')
    list_filter = ('module', 'action', 'actor_module')
    search_fields = ('object_label', 'item_label', 'chain_id')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False  # the log is a record of what happened: read-only
