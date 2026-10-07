from django.contrib import admin

from .models import Notification, Rule, Run


@admin.register(Rule)
class RuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'trigger', 'active', 'updated_at')
    list_filter = ('active', 'trigger')


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    list_display = ('rule', 'event', 'object_label', 'status', 'created_at')
    list_filter = ('status',)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'message', 'read', 'created_at')
    list_filter = ('read',)
