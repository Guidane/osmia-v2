from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from orders.models import Order

from .models import Vendor


class VendorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        self.client.login(username='admin', password='admin')

    def test_create_with_contacts_list_and_orders(self):
        resp = self.client.post(reverse('vendors:create'), {
            'name': 'Acme', 'code': 'ACM', 'kind': 'distributor', 'city': 'Springfield', 'is_active': 'on',
            'contacts-TOTAL_FORMS': '2', 'contacts-INITIAL_FORMS': '0',
            'contacts-0-name': 'Wile E.', 'contacts-0-role': 'Sales', 'contacts-0-email': 'wile@example.com',
            'contacts-1-name': '',
        })
        acme = Vendor.objects.get(name='Acme')
        self.assertRedirects(resp, acme.get_absolute_url())
        self.assertEqual(list(acme.contacts.values_list('name', flat=True)), ['Wile E.'])
        page = self.client.get(acme.get_absolute_url())
        self.assertContains(page, 'wile@example.com')
        self.assertContains(page, 'Springfield')
        listing = self.client.get(reverse('vendors:list') + '?q=wile')
        self.assertContains(listing, 'Acme')
        self.assertNotContains(listing, 'Mouser')
        # The demo order's supplier is a vendor, and the vendor page lists it.
        csc = Vendor.objects.get(name='Connector Supply Co.')
        order = Order.objects.get(supplier=csc)
        self.assertContains(self.client.get(csc.get_absolute_url()), order.number)
        self.assertContains(self.client.get(order.get_absolute_url()), csc.get_absolute_url())
        acme.is_active = False
        acme.save()
        self.assertNotContains(self.client.get(reverse('vendors:list')), 'Acme')
        self.assertContains(self.client.get(reverse('vendors:list') + '?inactive=1'), 'Acme')
        self.assertEqual(self.client.get(reverse('vendors:edit', args=[acme.pk])).status_code, 200)
