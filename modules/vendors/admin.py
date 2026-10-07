from django.contrib import admin

from .models import Contact, Vendor


class ContactInline(admin.TabularInline):
    model = Contact
    extra = 0


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'kind', 'city', 'country', 'account_number', 'is_active')
    list_filter = ('kind', 'is_active')
    search_fields = ('name', 'code', 'account_number', 'city', 'country')
    inlines = [ContactInline]
