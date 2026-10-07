from django.contrib import admin

from .models import HarnessProject, HarnessProjectVersion, SignalRule

admin.site.register(SignalRule)


class VersionInline(admin.TabularInline):
    model = HarnessProjectVersion
    extra = 0
    readonly_fields = ('version', 'created_by', 'created_at')
    fields = readonly_fields


@admin.register(HarnessProject)
class HarnessProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'version', 'created_by', 'updated_at')
    inlines = [VersionInline]
