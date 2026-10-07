from django.contrib import admin

from .models import Order, OrderLine


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('number', 'supplier', 'status', 'budget', 'created_at')
    list_filter = ('status',)
    search_fields = ('number', 'supplier__name')
    readonly_fields = ('number', 'status', 'placed_at', 'received_at')
    inlines = [OrderLineInline]
