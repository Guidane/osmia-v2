import json
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from . import lookup
from .models import Attribute, Category, MatingFamily, Part

# A trimmed Mouser "search/partnumber" response in the documented format.
MOUSER_RESPONSE = {
    'Errors': [],
    'SearchResults': {
        'NumberOfResult': 2,
        'Parts': [
            {'MouserPartNumber': '571-OTHER', 'ManufacturerPartNumber': 'OTHER-1', 'Description': 'Not this one'},
            {
                'MouserPartNumber': '571-5747461-3',
                'ManufacturerPartNumber': '5747461-3',
                'Manufacturer': 'TE Connectivity',
                'Description': 'D-Sub Standard Connectors 25P RCPT',
                'Category': 'D-Sub Standard Connectors',
                'DataSheetUrl': 'https://example.com/5747461.pdf',
                'ProductDetailUrl': 'https://www.mouser.com/ProductDetail/571-5747461-3',
                'ROHSStatus': 'RoHS Compliant',
                'LifecycleStatus': '',
                'ProductAttributes': [
                    {'AttributeName': 'Number of Positions', 'AttributeValue': '25 Position'},
                    {'AttributeName': 'Gender', 'AttributeValue': 'Receptacle (Female)'},
                    {'AttributeName': 'Packaging', 'AttributeValue': 'Tray'},
                    {'AttributeName': 'Packaging', 'AttributeValue': 'Bulk'},
                ],
                'PriceBreaks': [
                    {'Quantity': 10, 'Price': '$2.10', 'Currency': 'USD'},
                    {'Quantity': 1, 'Price': '$2.45', 'Currency': 'USD'},
                ],
            },
        ],
    },
}


class FakeResponse:
    def __init__(self, data):
        self.body = json.dumps(data).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class LookupParsingTests(TestCase):
    def test_parse_mouser_picks_exact_part(self):
        info = lookup.parse_mouser('5747461-3', MOUSER_RESPONSE)
        self.assertEqual(info.manufacturer, 'TE Connectivity')
        self.assertFalse(hasattr(info, 'unit_price'))  # prices are never looked up
        self.assertEqual(info.attributes['Number of Positions'], '25 Position')
        self.assertEqual(info.attributes['Packaging'], 'Tray, Bulk')
        self.assertEqual(info.attributes['Manufacturer'], 'TE Connectivity')
        self.assertEqual(info.attributes['Datasheet'], 'https://example.com/5747461.pdf')
        self.assertNotIn('Lifecycle', info.attributes)  # empty values are skipped

    def test_parse_mouser_errors_and_empty(self):
        with self.assertRaises(lookup.LookupFailed):
            lookup.parse_mouser('x', {'Errors': [{'Message': 'Invalid unique identifier.'}]})
        self.assertIsNone(lookup.parse_mouser('x', {'Errors': [], 'SearchResults': {'Parts': []}}))

    @override_settings(OSMIA_MOUSER_API_KEY='')
    def test_not_configured(self):
        with self.assertRaises(lookup.LookupNotConfigured):
            lookup.lookup('5747461-3')

    @override_settings(OSMIA_MOUSER_API_KEY='test-key')
    def test_request_sent_to_mouser(self):
        with mock.patch('urllib.request.urlopen', return_value=FakeResponse(MOUSER_RESPONSE)) as urlopen:
            info = lookup.lookup('5747461-3')
        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.startswith(lookup.MOUSER_URL + '?apiKey=test-key'))
        self.assertEqual(json.loads(request.data)['SearchByPartRequest'],
                         {'mouserPartNumber': '5747461-3', 'partSearchOptions': 'Exact'})
        self.assertEqual(info.source, 'Mouser')


class LookupViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        self.client.login(username='admin', password='admin')

    @override_settings(OSMIA_MOUSER_API_KEY='test-key')
    def test_lookup_endpoint(self):
        url = reverse('inventory:part_lookup')
        with mock.patch('urllib.request.urlopen', return_value=FakeResponse(MOUSER_RESPONSE)):
            data = self.client.get(url, {'part_number': '5747461-3'}).json()
        self.assertEqual(data['part']['description'], 'D-Sub Standard Connectors 25P RCPT')
        self.assertEqual(self.client.get(url).status_code, 400)
        with mock.patch('urllib.request.urlopen', return_value=FakeResponse({'Errors': [], 'SearchResults': {'Parts': []}})):
            self.assertEqual(self.client.get(url, {'part_number': 'nope'}).status_code, 404)

    @override_settings(OSMIA_MOUSER_API_KEY='')
    def test_lookup_endpoint_explains_missing_key(self):
        resp = self.client.get(reverse('inventory:part_lookup'), {'part_number': 'X'})
        self.assertEqual(resp.status_code, 503)
        self.assertIn('OSMIA_MOUSER_API_KEY', resp.json()['error'])

    def test_add_missing_attributes_then_save(self):
        connectors = Category.objects.get(name='Connectors')
        existing = connectors.attributes.get(name='pins')
        resp = self.client.post(
            reverse('inventory:category_add_attributes', args=[connectors.pk]),
            json.dumps({'names': ['Number of Positions', 'PINS', 'Number of Positions', '']}), content_type='application/json',
        )
        attrs = {a['name']: a['id'] for a in resp.json()['attributes']}
        self.assertEqual(attrs['pins'], existing.pk)  # matched case-insensitively, not duplicated
        positions = Attribute.objects.get(category=connectors, name='Number of Positions')
        self.assertEqual(attrs['Number of Positions'], positions.pk)

        # The part form now accepts a value for the new attribute.
        socket = Part.objects.get(part_number='DB25-F')
        self.client.post(reverse('inventory:part_edit', args=[socket.pk]), {
            'part_number': socket.part_number, 'name': socket.name, 'category': connectors.pk, 'unit': 'pcs',
            'cost': '2.60', 'reorder_level': '10',
            'is_active': 'on', f'attr_{positions.pk}': '25 Position',
        })
        self.assertEqual(socket.attribute_values.get(attribute=positions).value, '25 Position')

    def test_part_form_has_lookup(self):
        resp = self.client.get(reverse('inventory:part_create'))
        self.assertContains(resp, 'id="btn-lookup"')
        self.assertContains(resp, 'data-attr-name="thread"')


class PartLinkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        self.client.login(username='admin', password='admin')

    def test_mates_with_accepts_and_tools(self):
        from tools.models import Tool
        plug, socket = Part.objects.get(part_number='DB25-M'), Part.objects.get(part_number='DB25-F')
        contact = Part.objects.get(part_number='DS-PIN-C')
        self.assertEqual(list(plug.mates_with), [socket])  # the other side of the family
        self.assertEqual(list(socket.mates_with), [plug])  # both ways
        self.assertIn(contact, plug.fits.all())
        self.assertIn(plug, contact.fits_into.all())       # the other way round reads "fits into"
        page = self.client.get(contact.get_absolute_url())
        self.assertContains(page, 'Fits into')
        self.assertContains(page, 'DB25-M')
        self.assertContains(page, 'D-sub crimp tool')
        self.assertNotContains(page, 'Unit cost')
        # Edited on the part's form: the links are picked with the part picker.
        crimp = Tool.objects.get(name='D-sub crimp tool')
        strap = Part.objects.get(part_number='ESD-STRAP')
        edit = self.client.get(reverse('inventory:part_edit', args=[strap.pk]))
        self.assertContains(edit, 'data-part-picker')
        self.assertContains(edit, f'data-exclude="{strap.pk}"')
        self.client.post(reverse('inventory:part_edit', args=[strap.pk]), {
            'part_number': strap.part_number, 'name': strap.name, 'category': strap.category_id, 'unit': 'pair',
            'is_active': 'on', 'mating_family': plug.mating_family_id, 'mating_side': 'socket',
            'fits': [contact.pk], 'tools': [crimp.pk],
        })
        strap.refresh_from_db()
        self.assertEqual(list(strap.mates_with), [plug])
        self.assertEqual(list(strap.fits.all()), [contact])
        self.assertEqual(list(strap.tools.all()), [crimp])
        self.assertContains(self.client.get(reverse('inventory:part_list')), 'D-sub DB25 socket')

    def test_part_search_and_quick_create(self):
        found = self.client.get(reverse('inventory:part_search') + '?q=Arcol').json()['parts']
        self.assertEqual([p['part_number'] for p in found], ['RES-100R-50W'])
        resp = self.client.post(reverse('inventory:part_quick_create'), {'part_number': ' QX-1 ', 'name': 'Quick one'},
                                content_type='application/json')
        self.assertEqual(resp.json()['part']['part_number'], 'QX-1')
        self.assertEqual(self.client.post(reverse('inventory:part_quick_create'), {}, content_type='application/json').status_code, 400)
        self.assertNotContains(self.client.get(reverse('inventory:part_create')), 'reorder')


class MatingFamilyTests(TestCase):
    def setUp(self):
        from users.models import User
        User.objects.create_superuser('admin', password='admin')
        self.client.login(username='admin', password='admin')
        self.dsub = MatingFamily.objects.create(name='D-sub DB9')
        self.pin = Part.objects.create(part_number='DB9-P', mating_family=self.dsub, mating_side='pin')
        self.pin2 = Part.objects.create(part_number='DB9-P2', mating_family=self.dsub, mating_side='pin')
        self.socket = Part.objects.create(part_number='DB9-S', mating_family=self.dsub, mating_side='socket')
        self.other = Part.objects.create(part_number='M12-S', mating_family=MatingFamily.objects.create(name='M12'),
                                         mating_side='socket')

    def test_a_part_mates_with_the_other_side_of_its_family(self):
        self.assertEqual(set(self.socket.mates_with), {self.pin, self.pin2})
        self.assertEqual(list(self.pin.mates_with), [self.socket])  # not the other pin, not M12
        self.assertEqual(list(Part.objects.create(part_number='LOOSE').mates_with), [])
        unset = Part.objects.create(part_number='DB9-?', mating_family=self.dsub)
        self.assertEqual(set(unset.mates_with), {self.pin, self.pin2, self.socket})  # until its side is set

    def test_the_form_needs_a_side_with_a_family(self):
        url = reverse('inventory:part_create')
        data = {'part_number': 'DB9-X', 'unit': 'pcs', 'is_active': 'on', 'mating_family': self.dsub.pk}
        self.assertContains(self.client.post(url, data), 'Pick pin or socket')
        self.client.post(url, {**data, 'mating_side': 'pin'})
        self.assertEqual(Part.objects.get(part_number='DB9-X').mates_with.get(), self.socket)
        # Without a family the side is dropped.
        self.client.post(url, {'part_number': 'DB9-Y', 'unit': 'pcs', 'mating_side': 'pin'})
        self.assertEqual(Part.objects.get(part_number='DB9-Y').mating_side, '')

    def test_family_pages(self):
        page = self.client.get(self.dsub.get_absolute_url())
        self.assertContains(page, 'DB9-P2')
        self.assertContains(page, 'DB9-S')
        self.assertContains(self.client.get(reverse('inventory:family_list')), 'D-sub DB9')
        self.assertContains(self.client.get(self.pin.get_absolute_url()), 'DB9-S')
        self.client.post(reverse('inventory:family_create'), {'name': 'Molex Micro-Fit'})
        self.assertTrue(MatingFamily.objects.filter(name='Molex Micro-Fit').exists())
        self.client.post(reverse('inventory:family_delete', args=[self.dsub.pk]))
        self.pin.refresh_from_db()
        self.assertIsNone(self.pin.mating_family)  # the part stays
