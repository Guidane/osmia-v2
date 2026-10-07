from django.contrib import admin

from .models import Location, PartStock, StockItem, StockMove


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'parent')
    search_fields = ('name', 'code')


@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ('part', 'location', 'quantity')
    search_fields = ('part__part_number',)
    readonly_fields = ('quantity',)  # changed by stock moves only


@admin.register(PartStock)
class PartStockAdmin(admin.ModelAdmin):
    list_display = ('part', 'reorder_level', 'average_cost')
    readonly_fields = ('average_cost',)


@admin.register(StockMove)
class StockMoveAdmin(admin.ModelAdmin):
    # Moves change stock levels, so they are read-only here; create them in the app.
    list_display = ('created_at', 'part', 'move_type', 'delta', 'location', 'task', 'user')
    list_filter = ('move_type',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
