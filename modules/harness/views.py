"""The harness designer page and the JSON API it talks to.

The API keeps the stand-alone designer's shapes (see harness/static/harness/
designer.js). Devices and users come from Osmia's Devices and Users modules and
are read-only here: devices are created and edited only in the Devices module.
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods
from django.views.generic import ListView

from devices.models import Device

from .models import HarnessProject, SignalRule


class ProjectListView(LoginRequiredMixin, ListView):
    model = HarnessProject
    template_name = 'harness/harnessproject_list.html'  # the queryset becomes a list

    def get_queryset(self):
        projects = list(HarnessProject.objects.select_related('created_by').prefetch_related('images'))
        for p in projects:
            p.counts = p.stats()
        return projects


@login_required
def designer(request):
    return render(request, 'harness/designer.html', {
        'api_base': reverse('harness:api_devices').removesuffix('/devices'),
        'users_url': reverse('users:list'),
        'devices_url': reverse('devices:list'),
        'devices_create_url': reverse('devices:create'),
        # '0' is replaced by the open project's id
        'images_url': reverse('harness:project_images', args=[0]),
    })


def _json_body(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return None


# -- Devices (read-only) -------------------------------------------------------

@login_required
@require_GET
def api_devices(request):
    devices = Device.objects.prefetch_related('connectors__pins', 'connectors__part')
    return JsonResponse([d.to_harness() for d in devices], safe=False)


@login_required
@require_GET
def api_device(request, pk):
    device = get_object_or_404(Device, pk=pk)
    version = request.GET.get('version')
    if version:
        snap = device.versions.filter(version=version).first()
        if snap is None:
            raise Http404
        return JsonResponse({**snap.data, 'id': str(device.pk), 'version': snap.version, 'url': device.get_absolute_url()})
    return JsonResponse(device.to_harness())


# -- Projects ------------------------------------------------------------------

@login_required
@require_http_methods(['GET', 'POST'])
def api_projects(request):
    if request.method == 'GET':
        return JsonResponse([p.to_designer() for p in HarnessProject.objects.prefetch_related('versions')], safe=False)
    data = _json_body(request)
    if not isinstance(data, dict):
        return HttpResponseBadRequest('Expected a JSON object.')
    project = HarnessProject.objects.filter(pk=data.get('id') or None).first() or HarnessProject(created_by=request.user)
    project.save_version(data, request.user)
    return JsonResponse(project.to_designer())


@login_required
@require_http_methods(['GET', 'DELETE'])
def api_project(request, pk):
    project = get_object_or_404(HarnessProject, pk=pk)
    if request.method == 'DELETE':
        project.delete()
        return JsonResponse({'deleted': True})
    version = request.GET.get('version')
    if version and not project.versions.filter(version=version).exists():
        raise Http404
    return JsonResponse(project.to_designer(int(version) if version else None))


@login_required
def api_project_versions(request, pk):
    project = get_object_or_404(HarnessProject, pk=pk)
    return JsonResponse({'versions': sorted(project.versions.values_list('version', flat=True))})


@login_required
@require_http_methods(['POST'])
def api_project_activate(request, pk, version):
    project = get_object_or_404(HarnessProject, pk=pk)
    if not project.versions.filter(version=version).exists():
        return JsonResponse({'error': 'version not found'}, status=404)
    project.version = version
    project.name = project.data(version).get('name') or project.name
    project.save(update_fields=['version', 'name', 'updated_at'])
    return JsonResponse(project.to_designer())


# -- Settings ------------------------------------------------------------------

@login_required
@require_http_methods(['GET', 'POST'])
def api_signal_rules(request):
    if request.method == 'POST':
        data = _json_body(request)
        if not isinstance(data, dict) or not isinstance(data.get('pairs', []), list):
            return HttpResponseBadRequest('Expected {"pairs": [[a, b], ...]}.')
        SignalRule.replace_all(p for p in data.get('pairs', []) if isinstance(p, list) and len(p) == 2)
    return JsonResponse({'pairs': SignalRule.pairs()})


@login_required
def api_users(request):
    """Osmia's users, in the designer's {id, name} format (read-only here)."""
    users = get_user_model().objects.filter(is_active=True)
    return JsonResponse({'users': [{'id': str(u.pk), 'name': str(u)} for u in users]})


@login_required
def project_images(request, pk):
    """Photos of a project's harnesses (the designer itself has no room for them)."""
    project = get_object_or_404(HarnessProject, pk=pk)
    return render(request, 'harness/project_images.html', {'project': project})


# -- Extensions and ordering (the designer's connector tip and pinout popup) ----------

@login_required
@require_http_methods(['POST'])
def api_extension(request):
    """A new interconnect device with the exact pinout of the chosen connector
    (created in the Devices module's terms: versioned, with its pin map)."""
    data = _json_body(request) or {}
    device = get_object_or_404(Device, pk=data.get('device_id') or 0)
    connector = device.connectors.filter(designator=data.get('connector_id') or '').first()
    if connector is None:
        return JsonResponse({'error': 'That connector no longer exists on the device.'}, status=404)
    ext = Device.make_extension(device, connector, user=request.user)
    return JsonResponse(ext.to_harness())


@login_required
@require_GET
def api_parts(request):
    """Active parts, to pick a harness plug's part from."""
    from inventory.models import Part
    parts = Part.objects.filter(is_active=True).only('part_number', 'name')
    return JsonResponse({'parts': [{'part_number': p.part_number, 'name': p.name} for p in parts]})


@login_required
@require_http_methods(['POST'])
def api_order(request):
    """A draft order (Orders module) with the parts of a harness, e.g. its plugs."""
    from django.apps import apps
    if not apps.is_installed('orders'):
        return JsonResponse({'error': 'The Orders module is not installed.'}, status=400)
    from inventory.models import Part
    from orders.models import Order, OrderLine

    data = _json_body(request) or {}
    wanted = {}
    for line in data.get('lines') or []:
        number = str(line.get('part_number') or '').strip()
        try:
            qty = int(line.get('quantity') or 1)
        except (TypeError, ValueError):
            qty = 1
        if number and qty > 0:
            wanted[number] = wanted.get(number, 0) + qty
    parts = {p.part_number: p for p in Part.objects.filter(part_number__in=wanted)}
    missing = sorted(set(wanted) - set(parts))
    if not parts:
        return JsonResponse({'error': 'None of those parts are in Inventory: ' + ', '.join(missing) if missing else 'No parts to order.'}, status=400)
    note = str(data.get('note') or '')[:1000]
    if missing:
        note += ('\n' if note else '') + 'Not in Inventory, so not ordered: ' + ', '.join(missing)
    order = Order.objects.create(created_by=request.user, notes=note)
    for number, qty in wanted.items():
        if number in parts:
            stock = getattr(parts[number], 'stock', None)  # the Stock module's average cost, if it knows one
            price = stock.average_cost.quantize(Decimal('0.01')) if stock else Decimal(0)
            OrderLine.objects.create(order=order, part=parts[number], quantity=qty, unit_price=price)
    return JsonResponse({'number': order.number, 'url': order.get_absolute_url(), 'missing': missing,
                         'lines': sum(1 for n in wanted if n in parts)})
