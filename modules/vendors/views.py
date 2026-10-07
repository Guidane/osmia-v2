from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, ListView

from .forms import ContactFormSet, VendorForm
from .models import Vendor


class VendorListView(LoginRequiredMixin, ListView):
    model = Vendor

    def get_queryset(self):
        qs = Vendor.objects.annotate(contact_count=Count('contacts', distinct=True))
        g = self.request.GET
        for word in g.get('q', '').split():
            qs = qs.filter(Q(name__icontains=word) | Q(code__icontains=word) | Q(city__icontains=word)
                           | Q(country__icontains=word) | Q(account_number__icontains=word)
                           | Q(contacts__name__icontains=word)).distinct()
        if g.get('kind') in Vendor.Kind.values:
            qs = qs.filter(kind=g['kind'])
        if not g.get('inactive'):
            qs = qs.filter(is_active=True)
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, kinds=Vendor.Kind.choices)


class VendorDetailView(LoginRequiredMixin, DetailView):
    model = Vendor

    def get_context_data(self, **kwargs):
        # Orders is an optional module that links to vendors; Vendors doesn't depend on it.
        orders = self.object.orders.prefetch_related('lines')[:20] if hasattr(self.object, 'orders') else None
        return super().get_context_data(**kwargs, contacts=self.object.contacts.all(), orders=orders)


@login_required
def vendor_form(request, pk=None):
    vendor = get_object_or_404(Vendor, pk=pk) if pk else Vendor()
    form = VendorForm(request.POST or None, instance=vendor)
    formset = ContactFormSet(request.POST or None, instance=vendor, prefix='contacts')
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            vendor = form.save()
            formset.instance = vendor
            formset.save()
        messages.success(request, 'Vendor saved.')
        return redirect(vendor)
    return render(request, 'vendors/vendor_form.html', {
        'form': form, 'formset': formset, 'vendor': vendor,
        'heading': f'Edit {vendor}' if pk else 'New vendor',
        'cancel_url': vendor.get_absolute_url() if pk else None,
    })
