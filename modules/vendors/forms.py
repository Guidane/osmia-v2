from django import forms

from .models import Contact, Vendor


class VendorForm(forms.ModelForm):
    class Meta:
        model = Vendor
        fields = ['name', 'code', 'kind', 'website', 'email', 'phone', 'street', 'postcode', 'city', 'country',
                  'account_number', 'tax_id', 'payment_terms', 'currency', 'lead_time_days', 'notes', 'is_active']
        widgets = {'notes': forms.Textarea(attrs={'rows': 3})}


ContactFormSet = forms.inlineformset_factory(
    Vendor, Contact, fields=['name', 'role', 'email', 'phone'], extra=2, can_delete=True,
)
