from django.contrib import admin

from .models import Department, Membership


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'parent')
    search_fields = ('name',)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'department')
    list_filter = ('department',)
