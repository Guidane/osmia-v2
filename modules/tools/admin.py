from django.contrib import admin

from .models import Tool


@admin.register(Tool)
class ToolAdmin(admin.ModelAdmin):
    list_display = ('name', 'part_number', 'kind', 'manufacturer', 'storage', 'is_active')
    list_filter = ('kind', 'is_active')
    search_fields = ('name', 'part_number', 'manufacturer', 'asset_tag')
