from django.contrib import admin

from .models import Connector, Device, DeviceVersion, Pin


class ConnectorInline(admin.TabularInline):
    model = Connector
    extra = 0
    show_change_link = True


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ('name', 'part_number', 'origin', 'role', 'version', 'responsible')
    list_filter = ('origin', 'role')
    search_fields = ('name', 'part_number', 'manufacturer', 'model_number', 'asset_tag')
    inlines = [ConnectorInline]


class PinInline(admin.TabularInline):
    model = Pin
    extra = 0


@admin.register(Connector)
class ConnectorAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'side', 'part')
    inlines = [PinInline]


@admin.register(DeviceVersion)
class DeviceVersionAdmin(admin.ModelAdmin):
    list_display = ('device', 'version', 'created_by', 'created_at')
    readonly_fields = ('device', 'version', 'data', 'created_by', 'created_at')
