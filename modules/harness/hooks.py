from core import hooks

from .models import HarnessProject


@hooks.register('device_detail_panels')
def device_projects(request, device):
    projects = [p for p in HarnessProject.objects.prefetch_related('versions') if str(device.pk) in p.device_ids()]
    return hooks.panel('Harness projects', 'harness/_device_panel.html', {'projects': projects}, request)
