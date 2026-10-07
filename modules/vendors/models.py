from django.db import models
from django.urls import reverse


class Vendor(models.Model):
    """A company we buy parts, tools or services from. Orders pick their supplier from here."""

    class Kind(models.TextChoices):
        DISTRIBUTOR = 'distributor', 'Distributor'
        MANUFACTURER = 'manufacturer', 'Manufacturer'
        SERVICE = 'service', 'Service provider'
        OTHER = 'other', 'Other'

    name = models.CharField(max_length=200, unique=True)
    code = models.CharField(max_length=30, blank=True, help_text='A short code of our own, e.g. "MOU".')
    kind = models.CharField('Type', max_length=20, choices=Kind, default=Kind.DISTRIBUTOR)
    website = models.URLField(blank=True)
    email = models.EmailField(blank=True, help_text='The general or sales address.')
    phone = models.CharField(max_length=50, blank=True)
    street = models.CharField('Address', max_length=200, blank=True)
    postcode = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    account_number = models.CharField(max_length=100, blank=True, help_text='Our customer number with them.')
    tax_id = models.CharField('VAT / tax ID', max_length=50, blank=True)
    payment_terms = models.CharField(max_length=100, blank=True, help_text='e.g. "30 days net".')
    currency = models.CharField(max_length=3, blank=True, help_text='e.g. EUR, USD.')
    lead_time_days = models.PositiveIntegerField('Lead time (days)', null=True, blank=True,
                                                 help_text='How long they usually take to deliver.')
    notes = models.TextField(blank=True)
    is_active = models.BooleanField('Active', default=True, help_text='Untick when we no longer buy from them.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('vendors:detail', args=[self.pk])

    @property
    def address_lines(self):
        town = ' '.join(filter(None, (self.postcode, self.city)))
        return [line for line in (self.street, town, self.country) if line]


class Contact(models.Model):
    """A person at a vendor."""

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=200)
    role = models.CharField(max_length=100, blank=True, help_text='e.g. Sales, Support, Accounts.')
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.vendor})'
