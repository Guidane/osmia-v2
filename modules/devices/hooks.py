from django.urls import reverse

from core import hooks

from .models import Connector, Device


@hooks.register('dashboard_widgets')
def device_count(request):
    return hooks.Widget('Devices', Device.objects.count(), reverse('devices:list'))


@hooks.register('assembly_detail_panels')
def assembly_device(request, assembly):
    if assembly.assembly_type != 'device':
        return None
    device = Device.objects.filter(assembly=assembly).first()
    return hooks.panel('Device & connectors', 'devices/_assembly_panel.html', {
        'assembly': assembly, 'device': device,
        'connectors': device.connectors.prefetch_related('pins') if device else [],
    }, request, order=5)


@hooks.register('part_detail_panels')
def part_as_connector(request, part):
    uses = Connector.objects.filter(part=part).select_related('device')
    if not uses:
        return None
    return hooks.panel('Used as a connector on', 'devices/_part_panel.html', {'uses': uses}, request)
