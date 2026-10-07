from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView

from core.trees import link_parents, sorted_by_path
from inventory.models import Part

from .forms import LocationForm, PartStockForm, StockMoveForm
from .models import Location, PartStock, StockItem, StockMove, on_hand, qty


def move_locations():
    """Every location, in tree order, for "move to" pickers."""
    return sorted_by_path(link_parents(Location.objects.all()))


def _back(request, default):
    back = request.POST.get('next') or request.GET.get('next') or ''
    if url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return back
    return default


# -- Stock: what's where ------------------------------------------------------------

class StockListView(LoginRequiredMixin, ListView):
    """Every stock item (a part at a location); select rows to move them."""
    model = StockItem
    template_name = 'stock/stock_list.html'
    paginate_by = 200

    def get_queryset(self):
        qs = StockItem.objects.select_related('part__category', 'part__stock', 'location').prefetch_related('part__images')
        g = self.request.GET
        if not g.get('empty'):
            qs = qs.exclude(quantity=0)
        for word in g.get('q', '').split():
            qs = qs.filter(Q(part__part_number__icontains=word) | Q(part__name__icontains=word)
                           | Q(location__name__icontains=word) | Q(location__code__iexact=word))
        if g.get('location') == 'none':
            qs = qs.filter(location=None)
        elif str(g.get('location', '')).isdigit():
            location = Location.objects.filter(pk=g['location']).first()
            if location:
                qs = qs.filter(location_id__in={location.pk, *location.descendant_ids()})
        if g.get('low'):
            qs = qs.filter(part__in=low_stock_parts())
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, locations=move_locations(), move_locations=move_locations(),
                                        low_parts=low_stock_parts())


def low_stock_parts():
    """Parts at or below their reorder level, all locations together."""
    totals = dict(StockItem.objects.values_list('part').annotate(t=Sum('quantity')))
    return [ps.part for ps in PartStock.objects.select_related('part').filter(part__is_active=True)
            if ps.reorder_level and totals.get(ps.part_id, Decimal(0)) <= ps.reorder_level]


# -- Moves ----------------------------------------------------------------------------

class MoveListView(LoginRequiredMixin, ListView):
    model = StockMove
    paginate_by = 100

    def get_queryset(self):
        qs = StockMove.objects.select_related('part', 'task', 'user', 'location', 'from_location', 'to_location')
        g = self.request.GET
        if g.get('type') in StockMove.Type.values:
            qs = qs.filter(move_type=g['type'])
        if g.get('part'):
            qs = qs.filter(part_id=g['part'])
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, types=StockMove.Type.choices)


class MoveCreateView(LoginRequiredMixin, FormView):
    form_class = StockMoveForm
    template_name = 'core/form.html'
    extra_context = {'heading': 'New stock move'}

    def get_initial(self):
        g = self.request.GET
        initial = {k: g[k] for k in ('part', 'task', 'move_type', 'location') if g.get(k)}
        if g.get('task') and 'move_type' not in initial:
            initial['move_type'] = StockMove.Type.OUT
        return initial

    def form_valid(self, form):
        try:
            move = form.save(self.request.user)
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)
        part = move.part
        messages.success(self.request, f'Recorded: {move}. On hand: {qty(on_hand(part))} {part.unit}.')
        return redirect(_back(self.request, part.get_absolute_url()))


@login_required
@require_POST
def transfer(request):
    """Move the selected stock items to another location (from the stock list,
    a location's page or a part's page)."""
    back = _back(request, reverse('stock:list'))
    location = Location.objects.filter(pk=request.POST.get('location') or None).first()
    ids = {int(p) for p in request.POST.getlist('items') if p.isdigit()}
    if location is None:
        messages.error(request, 'Pick the location to move to.')
        return redirect(back)
    if not ids:
        messages.error(request, 'Select the rows to move first.')
        return redirect(back)
    moved, already = [], 0
    with transaction.atomic():
        for item in StockItem.objects.filter(pk__in=ids).select_related('part', 'location'):
            if StockMove.transfer(item, location, user=request.user, note=request.POST.get('note', '').strip()[:255]):
                moved.append(item.part.part_number)
            else:
                already += 1
    where = f'{location.code} ({location.name})' if location.code else location.full_path()
    if moved:
        shown = ', '.join(dict.fromkeys(moved[:5])) + (f' and {len(moved) - 5} more' if len(moved) > 5 else '')
        messages.success(request, f'Moved {shown} to {where}.')
    if already:
        messages.info(request, f'{already} row{"s were" if already != 1 else " was"} already in {where}.')
    return redirect(back)


@login_required
def part_settings(request, part_pk):
    """A part's reorder level (its average cost follows the receipts)."""
    part = get_object_or_404(Part, pk=part_pk)
    settings_ = PartStock.of(part)
    form = PartStockForm(request.POST or None, instance=settings_)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Stock settings of {part.part_number} saved.')
        return redirect(part)
    return render(request, 'core/form.html', {'form': form, 'heading': f'Stock settings · {part.part_number}',
                                              'cancel_url': part.get_absolute_url()})


# -- Locations (nested) --------------------------------------------------------

class LocationListView(LoginRequiredMixin, ListView):
    model = Location
    template_name = 'stock/location_list.html'

    def get_queryset(self):
        nodes = link_parents(Location.objects.prefetch_related('images').annotate(
            own_parts=Count('items__part', filter=~Q(items__quantity=0), distinct=True),
            own_quantity=Sum('items__quantity'),
        ))
        # Totals include sub-locations, like the stock list's location filter.
        for n in nodes:
            n.part_count, n.quantity, n.sub_count = 0, Decimal(0), 0
        for n in nodes:
            for i, a in enumerate(reversed(n.ancestors())):
                a.part_count += n.own_parts
                a.quantity += n.own_quantity or 0
                if i:
                    a.sub_count += 1
        return sorted_by_path(nodes)

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            **kwargs, heading='Locations', create_url=reverse('stock:location_create'), filter_param='location',
            show_images=True, show_codes=True, show_stock=True, list_url=reverse('stock:list'),
        )


class LocationDetailView(LoginRequiredMixin, DetailView):
    model = Location

    def get_context_data(self, **kwargs):
        loc = self.object
        return super().get_context_data(
            **kwargs,
            children=sorted_by_path(loc.children.annotate(part_count=Count('items', filter=~Q(items__quantity=0)))),
            items=loc.items.exclude(quantity=0).select_related('part__category').prefetch_related('part__images'),
            move_locations=move_locations(),
        )


class LocationCreateView(LoginRequiredMixin, CreateView):
    model = Location
    form_class = LocationForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('stock:location_list')
    extra_context = {'heading': 'New location'}

    def get_initial(self):
        return {'parent': self.request.GET.get('parent')}


class LocationUpdateView(LoginRequiredMixin, UpdateView):
    model = Location
    form_class = LocationForm
    template_name = 'core/form.html'

    def get_success_url(self):
        return self.object.get_absolute_url()

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, heading=f'Edit {self.object}', cancel_url=self.object.get_absolute_url())


@login_required
def location_generate(request, pk=None):
    """Make a block of sub-locations at once, e.g. rack rows × racks × shelves."""
    from . import locations as gen

    parent = get_object_or_404(Location, pk=pk) if pk else None
    rows = [{'name': 'Rack row', 'count': 4, 'style': 'A', 'start': ''},
            {'name': 'Rack', 'count': 6, 'style': '1', 'start': ''},
            {'name': 'Shelf', 'count': 4, 'style': 'A', 'start': ''}]
    errors = []
    if request.method == 'POST':
        rows = [{key: request.POST.get(f'level-{i}-{key}', '') for key in ('name', 'count', 'style', 'start')}
                for i in range(gen.MAX_LEVELS + 2)]
        rows = [r for r in rows if r['name'].strip() or r['count'].strip()]
        chosen = request.POST.get('parent')
        parent = Location.objects.filter(pk=chosen).first() if chosen else None
        try:
            levels = gen.clean_levels(rows)
        except ValidationError as exc:
            errors = exc.messages
        else:
            created, existing = gen.generate(parent, levels)
            note = f' ({existing} already existed and were kept)' if existing else ''
            messages.success(request, f'Created {created} location{"s" if created != 1 else ""}{note}.')
            return redirect(parent.get_absolute_url() if parent else reverse('stock:location_list'))
    all_locations = sorted_by_path(Location.objects.all())
    return render(request, 'stock/location_generate.html', {
        'parent': parent, 'rows': rows, 'errors': errors, 'styles': gen.STYLES,
        'locations': all_locations,
        'codes': {str(loc.pk): loc.code for loc in all_locations},
        'max_locations': gen.MAX_LOCATIONS,
    })


@login_required
def location_delete(request, pk):
    """Delete a location and its sub-locations, but only while no stock is kept in any of them."""
    loc = get_object_or_404(Location, pk=pk)
    ids = {loc.pk, *loc.descendant_ids()}
    items = StockItem.objects.filter(location_id__in=ids).exclude(quantity=0).select_related('part', 'location')
    if request.method == 'POST' and not items.exists():
        parent = loc.parent
        doomed = list(Location.objects.filter(pk__in=ids))
        with transaction.atomic():
            StockItem.objects.filter(location_id__in=ids, quantity=0).delete()
            # Children first: a location with sub-locations can't be deleted before them.
            for node in sorted(doomed, key=lambda n: n.full_path().count(' > '), reverse=True):
                node.delete()
        messages.success(request, f'Deleted {loc.full_path()}' + (f' and {len(ids) - 1} sub-location{"s" if len(ids) != 2 else ""}.' if len(ids) > 1 else '.'))
        return redirect(_back(request, parent.get_absolute_url() if parent else reverse('stock:location_list')))
    return render(request, 'stock/location_delete.html', {
        'location': loc, 'sub_count': len(ids) - 1, 'items': items[:50], 'part_count': items.count(),
    })
