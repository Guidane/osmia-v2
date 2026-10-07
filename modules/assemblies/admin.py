from django.contrib import admin

from .models import Assembly, AssemblyComponent, TaskLink


class ComponentInline(admin.TabularInline):
    model = AssemblyComponent
    fk_name = 'assembly'
    extra = 1
    autocomplete_fields = ('part',)


@admin.register(Assembly)
class AssemblyAdmin(admin.ModelAdmin):
    list_display = ('name', 'assembly_type', 'status', 'version', 'revision')
    list_filter = ('assembly_type', 'status')
    search_fields = ('name',)
    readonly_fields = ('revision',)
    inlines = [ComponentInline]


admin.site.register(TaskLink)
