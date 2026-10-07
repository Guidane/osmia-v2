import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from core import hooks
from core.trees import sorted_by_path

from . import lookup
from .forms import AttributeFormSet, CategoryForm, PartForm, QuickPartForm
from .models import Attribute, Category, Part


class PartListView(LoginRequiredMixin, ListView):
    model = Part
    paginate_by = 50

    def get_queryset(self):
        return search_parts(self.request.GET).select_related('category__parent').prefetch_related('images', 'mates_with', 'fits')

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, categories=Category.objects.select_related('parent'))


def search_parts(g):
    """Parts matching ?q= (part number, description, category, attribute
    values), ?category= (with its sub-categories) and ?archived=1."""
    qs = Part.objects.all()
    for word in g.get('q', '').split():
        qs = qs.filter(
            Q(part_number__icontains=word) | Q(name__icontains=word)
            | Q(category__name__icontains=word) | Q(attribute_values__value__icontains=word)
        )
    if str(g.get('category', '')).isdigit():
        category = Category.objects.filter(pk=g['category']).first()
        if category:
            qs = qs.filter(category_id__in={category.pk, *category.descendant_ids()})
    if not g.get('archived'):
        qs = qs.filter(is_active=True)
    return qs.distinct()


def part_json(p):
    return {'id': p.pk, 'part_number': p.part_number, 'name': p.name, 'unit': p.unit,
            'category': str(p.category) if p.category_id else '', 'url': p.get_absolute_url()}


@login_required
def part_search(request):
    """JSON for the part picker (orders, assemblies, part links): up to 50 parts."""
    parts = search_parts(request.GET).select_related('category__parent')[:50]
    return JsonResponse({'parts': [part_json(p) for p in parts]})


@login_required
@require_POST
def part_quick_create(request):
    """JSON in {part_number, name}: a new part with just those, for adding on the fly."""
    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        return HttpResponseBadRequest('Expected JSON.')
    form = QuickPartForm(data if isinstance(data, dict) else {})
    if not form.is_valid():
        return JsonResponse({'errors': {k: [str(e) for e in v] for k, v in form.errors.items()}}, status=400)
    part = form.save()
    return JsonResponse({'part': part_json(part)})


class PartDetailView(LoginRequiredMixin, DetailView):
    model = Part

    def get_context_data(self, **kwargs):
        p = self.object
        return super().get_context_data(
            **kwargs,
            attribute_values=p.attribute_values.select_related('attribute'),
            mates_with=p.mates_with.all(), fits=p.fits.all(), fits_into=p.fits_into.all(),
            tools=p.tools.all(),
            panels=hooks.collect('part_detail_panels', self.request, p),
        )


class PartFormMixin(LoginRequiredMixin):
    model = Part
    form_class = PartForm
    template_name = 'inventory/part_form.html'

    def form_valid(self, form):
        with transaction.atomic():
            response = super().form_valid(form)
        messages.success(self.request, 'Part saved.')
        return response


class PartCreateView(PartFormMixin, CreateView):
    extra_context = {'heading': 'New part'}

    def get_initial(self):
        return {k: self.request.GET[k] for k in ('category', 'part_number', 'name') if self.request.GET.get(k)}


class PartUpdateView(PartFormMixin, UpdateView):
    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, heading=f'Edit {self.object}', cancel_url=self.object.get_absolute_url())


@login_required
def part_lookup(request):
    """JSON: what an online provider knows about ?part_number=..."""
    part_number = request.GET.get('part_number', '').strip()
    if not part_number:
        return JsonResponse({'error': 'Enter a part number first.'}, status=400)
    try:
        info = lookup.lookup(part_number)
    except lookup.LookupNotConfigured as exc:
        return JsonResponse({'error': str(exc), 'not_configured': True}, status=503)
    except lookup.LookupFailed as exc:
        return JsonResponse({'error': str(exc)}, status=502)
    if info is None:
        return JsonResponse({'error': f'No part found for "{part_number}".'}, status=404)
    return JsonResponse({'part': info.as_dict()})


@login_required
@require_POST
def category_add_attributes(request, pk):
    """JSON in: {"names": [...]}; adds the missing ones to the category and
    returns every requested attribute as {name, id} (existing ones too)."""
    category = get_object_or_404(Category, pk=pk)
    try:
        names = json.loads(request.body or b'{}').get('names') or []
    except (ValueError, AttributeError):
        return HttpResponseBadRequest('Expected {"names": [...]}.')
    result = []
    for name in dict.fromkeys(str(n).strip()[:100] for n in names):
        if not name:
            continue
        attribute = (Attribute.objects.filter(category=category, name__iexact=name).first()
                     or Attribute.objects.create(category=category, name=name))
        result.append({'name': attribute.name, 'id': attribute.pk})
    return JsonResponse({'attributes': result})


# -- Categories (nested, with attribute definitions) --------------------------

class CategoryListView(LoginRequiredMixin, ListView):
    model = Category
    template_name = 'inventory/tree_list.html'

    def get_queryset(self):
        return sorted_by_path(Category.objects.select_related('parent').annotate(
            part_count=Count('parts', distinct=True), attribute_count=Count('attributes', distinct=True),
        ))

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            **kwargs, heading='Categories', create_url=reverse('inventory:category_create'),
            filter_param='category', show_attributes=True,
        )


@login_required
def category_form(request, pk=None):
    category = get_object_or_404(Category, pk=pk) if pk else Category()
    form = CategoryForm(request.POST or None, instance=category)
    formset = AttributeFormSet(request.POST or None, instance=category, prefix='attrs')
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            category = form.save()
            formset.instance = category
            formset.save()
        messages.success(request, 'Category saved.')
        return redirect('inventory:category_list')
    return render(request, 'inventory/category_form.html', {
        'form': form, 'formset': formset, 'category': category,
        'heading': f'Edit {category}' if pk else 'New category',
    })
