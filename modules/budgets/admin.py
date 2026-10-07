from django.contrib import admin

from .models import Budget, TaskBudget


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'amount', 'department')
    list_filter = ('department',)
    search_fields = ('name', 'budget_number')


admin.site.register(TaskBudget)
