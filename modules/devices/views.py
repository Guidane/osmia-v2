from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Q
from django.forms import modelformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from assemblies.models import Assembly
from core import hooks
from core.trees import natural_key

from .forms import ConnectorForm, DeviceForm, PinFormSet, SignalForm
from .models import Connector, Device, Pin, PinMap, Signal, TagOption


class DeviceListView(LoginRequiredMixin, ListView):
    model = Device

    def get_queryset(self):
        qs = Device.objects.select_related('responsible', 'assembly').prefetch_related('images').annotate(
            connector_count=Count('connectors', distinct=True), pin_count=Count('connectors__pins'),
        )
        g = self.request.GET
        if g.get('q'):
            qs = qs.filter(
                Q(name__icontains=g['q']) | Q(part_number__icontains=g['q']) | Q(manufacturer__icontains=g['q'])
                | Q(model_number__icontains=g['q']) | Q(asset_tag__icontains=g['q'])
            )
        if g.get('origin') in Device.Origin.values:
            qs = qs.filter(origin=g['origin'])
        if g.get('role') in Device.Role.values:
            qs = qs.filter(role=g['role'])
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, origins=Device.Origin.choices, roles=Device.Role.choices)


# What the pin table can be sorted by (?sort=tag2, or ?sort=-tag2 for descending).
PIN_SORTS = {
    'pin': lambda p: p.label,
    'signal': lambda p: p.signal,
    'tag1': lambda p: p.tag1, 'tag2': lambda p: p.tag2, 'tag3': lambda p: p.tag3, 'tag4': lambda p: p.tag4,
    'set': lambda p: p.set_number,
    'set_type': lambda p: p.get_set_type_display() if p.set_type else '',
}


class DeviceDetailView(LoginRequiredMixin, DetailView):
    """Sidebar with the device's connectors; the right side shows the chosen
    connector's pins (?connector=<id>) or, by default, the device details."""

    model = Device

    def get_context_data(self, **kwargs):
        d = self.object
        connectors = list(d.connectors.select_related('part').prefetch_related('pins'))
        wanted = self.request.GET.get('connector', '')
        selected = next((c for c in connectors if str(c.pk) == wanted), None)
        context = {'connectors': connectors, 'selected': selected}
        if selected is not None:
            pins = list(selected.pins.all())
            sort = self.request.GET.get('sort', '')
            key, descending = sort.lstrip('-'), sort.startswith('-')
            if key in PIN_SORTS:
                get = PIN_SORTS[key]
                # Pins with a value first (in natural order, e.g. 2 before 10), empty ones last, in pin order.
                filled = sorted((p for p in pins if get(p) not in ('', None)), key=lambda p: natural_key(str(get(p))), reverse=descending)
                pins = filled + [p for p in pins if get(p) in ('', None)]
            n = selected.tag_column_count()
            context.update(pins=pins, sort=sort, sort_key=key if key in PIN_SORTS else '', sort_desc=descending,
                           tag_slice=f':{n}', sort_columns=[
                ('pin', 'Pin'), *[(f'tag{i}', f'Tag {i}') for i in range(1, n + 1)],
                ('signal', 'Signal'), ('set', 'Set'), ('set_type', 'Set type'),
            ])
        else:
            context.update(
                versions=d.versions.select_related('created_by')[:20],
                panels=hooks.collect('device_detail_panels', self.request, d),
                pin_maps=d.pin_maps.select_related('from_pin__connector', 'to_pin__connector') if d.is_interconnect else [],
            )
        return super().get_context_data(**kwargs, **context)


class DeviceFormMixin(LoginRequiredMixin):
    model = Device
    form_class = DeviceForm
    template_name = 'core/form.html'

    def form_valid(self, form):
        with transaction.atomic():
            response = super().form_valid(form)
            self.object.snapshot(self.request.user)
        messages.success(self.request, 'Device saved.')
        return response


class DeviceCreateView(DeviceFormMixin, CreateView):
    extra_context = {'heading': 'New device'}

    def get_initial(self):
        g = self.request.GET
        initial = {'responsible': self.request.user}
        if g.get('origin') == Device.Origin.EXTERNAL:
            initial.update(origin=Device.Origin.EXTERNAL, role=Device.Role.POWER_SUPPLY)
        return initial


class DeviceUpdateView(DeviceFormMixin, UpdateView):
    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, heading=f'Edit {self.object}', cancel_url=self.object.get_absolute_url())


@login_required
@require_POST
def device_from_assembly(request, assembly_pk):
    """Create the device record for an assembly of type "Device"."""
    assembly = get_object_or_404(Assembly, pk=assembly_pk, assembly_type=Assembly.Type.DEVICE)
    device = getattr(assembly, 'device', None)
    if device is None:
        device = Device.objects.create(name=assembly.name, assembly=assembly, responsible=request.user)
        device.snapshot(request.user)
        messages.success(request, 'Device created. Now add its connectors.')
    return redirect(device)


def part_info():
    """{part id: its details} for the connector editor, which shows the chosen part's details."""
    from inventory.models import Part
    info = {}
    for p in Part.objects.filter(is_active=True).select_related('category__parent').prefetch_related('attribute_values__attribute'):
        info[str(p.pk)] = {
            'part_number': p.part_number, 'name': p.name, 'url': p.get_absolute_url(),
            'category': p.category.full_path() if p.category else '',
            'attributes': [[v.attribute.name, v.value] for v in p.attribute_values.all() if v.value],
        }
    return info


@login_required
def connector_form(request, device_pk, pk=None):
    device = get_object_or_404(Device, pk=device_pk)
    if pk:
        connector = get_object_or_404(Connector, pk=pk, device=device)
    else:
        n = device.connectors.count() + 1
        connector = Connector(device=device, position=n, designator=f'J{n:02d}')
    form = ConnectorForm(request.POST or None, instance=connector)
    formset = PinFormSet(request.POST or None, instance=connector, prefix='pins')
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            connector = form.save()
            formset.instance = connector
            formset.save(commit=False)
            formset.save_tag_options(connector)
            for pin in formset.deleted_objects:
                pin.delete()
            deleted = set(formset.deleted_forms)
            pins = [f.instance for f in formset.forms if f not in deleted and (f.instance.pk or f.has_changed())]
            # Renumber in the order shown, clear of the unique (connector, position) constraint.
            Pin.objects.filter(connector=connector).update(position=F('position') + 100000)
            for i, pin in enumerate(pins, start=1):
                pin.connector, pin.position = connector, i
                pin.save()
            new_version = device.snapshot(request.user)
        messages.success(request, f'{connector.designator} saved.' + (f' Device is now v{device.version}.' if new_version else ''))
        if request.POST.get('continue'):
            return redirect('devices:connector_edit', device.pk, connector.pk)
        return redirect(f'{device.get_absolute_url()}?connector={connector.pk}')
    try:
        tag_count = max(1, min(4, int(form['tag_columns'].value() or 1)))
    except (TypeError, ValueError):
        tag_count = 1
    return render(request, 'devices/connector_form.html', {
        'device': device, 'connector': connector, 'form': form, 'formset': formset, 'part_info': part_info(),
        'tag_count': tag_count, 'tag_numbers': [1, 2, 3, 4],
        'heading': f'{device.name} · {connector.designator}' if pk else f'{device.name} · new connector',
    })


@login_required
@require_POST
def connector_delete(request, device_pk, pk):
    device = get_object_or_404(Device, pk=device_pk)
    connector = get_object_or_404(Connector, pk=pk, device=device)
    with transaction.atomic():
        connector.delete()
        device.snapshot(request.user)
    messages.success(request, f'{connector.designator} removed.')
    return redirect(device)


@login_required
@require_POST
def activate_version(request, pk, version):
    device = get_object_or_404(Device, pk=pk)
    device.activate_version(version)
    messages.success(request, f'Version {version} is current again.')
    return redirect(device)


@login_required
@require_POST
def connector_clone(request, device_pk, pk):
    """A copy of the connector and its pins, on the same device (next free J..)."""
    device = get_object_or_404(Device, pk=device_pk)
    connector = get_object_or_404(Connector, pk=pk, device=device)
    with transaction.atomic():
        copy = device.clone_connector(connector)
        device.snapshot(request.user)
    messages.success(request, f'Cloned {connector.designator} as {copy.designator}. Adjust it as needed.')
    return redirect('devices:connector_edit', device.pk, copy.pk)


# -- The shared signal list ------------------------------------------------------

SignalFormSet = modelformset_factory(Signal, form=SignalForm, extra=1, can_delete=True)


@login_required
def signals(request):
    """Signals are the same across all devices: edit the list here."""
    formset = SignalFormSet(request.POST or None, queryset=Signal.objects.all(), prefix='signals')
    if request.method == 'POST' and formset.is_valid():
        blocked = []
        with transaction.atomic():
            for form in formset.forms:
                if not form.has_changed() and not form.instance.pk:
                    continue
                if form in formset.deleted_forms:
                    if form.instance.pk:
                        used = form.instance.usage()
                        if used:
                            blocked.append(f'{form.instance.name} ({used} pin{"s" if used != 1 else ""})')
                        else:
                            form.instance.delete()
                    continue
                if form.instance.pk:
                    old = Signal.objects.get(pk=form.instance.pk)
                    new_name = form.cleaned_data['name']
                    form.instance.name = old.name
                    form.instance.description = form.cleaned_data.get('description', '')
                    form.instance.rename(new_name)
                elif form.cleaned_data.get('name'):
                    form.save()
        if blocked:
            messages.error(request, 'Still used, so not deleted: ' + ', '.join(blocked) + '. Change those pins first.')
        else:
            messages.success(request, 'Signals saved.')
        return redirect('devices:signals')
    usage = dict(Pin.objects.exclude(signal='').values_list('signal').annotate(n=Count('id')))
    return render(request, 'devices/signals.html', {'formset': formset, 'usage': usage})


# -- Interconnects: which input pin goes to which output pin -------------------------

@login_required
def pin_mapping(request, pk):
    device = get_object_or_404(Device, pk=pk)
    connectors = list(device.connectors.prefetch_related('pins'))
    pins = {str(p.pk): p for c in connectors for p in c.pins.all()}
    if request.method == 'POST':
        pairs = []
        if request.POST.get('by_position'):
            a = next((c for c in connectors if str(c.pk) == request.POST.get('from_connector')), None)
            b = next((c for c in connectors if str(c.pk) == request.POST.get('to_connector')), None)
            if a and b and a != b:
                pairs = [(m.from_pin_id, m.to_pin_id) for m in device.pin_maps.all()]
                pairs += list(zip([p.pk for p in a.pins.all()], [p.pk for p in b.pins.all()]))
            else:
                messages.error(request, 'Pick two different connectors to map pin by pin.')
                return redirect('devices:pin_mapping', device.pk)
        else:
            for f, t in zip(request.POST.getlist('from'), request.POST.getlist('to')):
                if f in pins and t in pins and f != t:
                    pairs.append((pins[f].pk, pins[t].pk))
        with transaction.atomic():
            device.pin_maps.all().delete()
            for f, t in dict.fromkeys(pairs):
                PinMap.objects.create(device=device, from_pin_id=f, to_pin_id=t)
            if device.role != Device.Role.INTERCONNECT:
                device.role = Device.Role.INTERCONNECT
                device.save(update_fields=['role'])
            new_version = device.snapshot(request.user)
        messages.success(request, f'Pin mapping saved ({len(dict.fromkeys(pairs))} pins).' + (f' Device is now v{device.version}.' if new_version else ''))
        return redirect(device)
    return render(request, 'devices/pin_mapping.html', {
        'device': device, 'connectors': connectors,
        'maps': device.pin_maps.select_related('from_pin__connector', 'to_pin__connector'),
    })


@login_required
@require_POST
def connector_import(request, device_pk, pk):
    """Edit pins > Import CSV: a pin table file updates or replaces the pins."""
    from . import pin_import

    device = get_object_or_404(Device, pk=device_pk)
    connector = get_object_or_404(Connector, pk=pk, device=device)
    upload = request.FILES.get('file')
    back = redirect('devices:connector_edit', device.pk, connector.pk)
    if upload is None:
        messages.error(request, 'Pick a CSV file to import.')
        return back
    try:
        rows, unknown_columns = pin_import.read_rows(upload)
        pins, problems = pin_import.clean_rows(rows)
        with transaction.atomic():
            result = pin_import.apply(connector, pins, replace=request.POST.get('mode') == 'replace')
            new_version = device.snapshot(request.user)
    except ValidationError as exc:
        messages.error(request, 'Nothing imported: ' + ' '.join(exc.messages))
        return back
    parts = [f'{result["updated"]} updated', f'{result["created"]} added']
    if result['removed']:
        parts.append(f'{result["removed"]} removed')
    messages.success(request, f'Imported {len(pins)} pins into {connector.designator}: ' + ', '.join(parts) + '.'
                     + (f' Device is now v{device.version}.' if new_version else ''))
    if result['added_signals']:
        messages.info(request, 'New signals added to Devices › Signals: ' + ', '.join(result['added_signals']) + '.')
    if unknown_columns:
        messages.info(request, 'Columns not used: ' + ', '.join(unknown_columns) + '.')
    for problem in problems[:20]:
        messages.warning(request, problem)
    return back


@login_required
def device_delete(request, pk):
    """Remove a device, unless a harness project uses it."""
    device = get_object_or_404(Device, pk=pk)
    projects = []
    from django.apps import apps
    if apps.is_installed('harness'):
        from harness.models import HarnessProject
        projects = [p for p in HarnessProject.objects.prefetch_related('versions') if str(device.pk) in p.device_ids()]
    if request.method == 'POST' and not projects:
        name = str(device)
        device.delete()
        messages.success(request, f'Removed {name}.')
        return redirect('devices:list')
    return render(request, 'devices/device_delete.html', {'device': device, 'projects': projects})
