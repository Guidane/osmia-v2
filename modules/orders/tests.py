import shutil
import tempfile
from decimal import Decimal
from io import BytesIO, StringIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image as PILImage

from budgets.models import Budget, Spending
from inventory.models import Part
from stock.models import Location, StockItem, on_hand
from vendors.models import Vendor

from .models import Order


class OrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())
        # These tests are about orders alone, not the demo automation rules.
        from automations.models import Rule
        Rule.objects.update(active=False)

    def setUp(self):
        self.client.login(username='admin', password='admin')
        self.order = Order.objects.get(supplier__name='Connector Supply Co.')
        self.acme = Vendor.objects.create(name='Acme')

    def test_numbering_and_total(self):
        self.assertTrue(self.order.number.startswith('PO-'))
        self.assertEqual(self.order.total, Decimal('20') * Decimal('2.60') + Decimal('10') * Decimal('2.40'))

    def test_receiving_books_stock_once_and_counts_against_budget(self):
        part = Part.objects.get(part_number='DB25-F')
        before = on_hand(part)
        budget = self.order.budget
        self.assertEqual(Spending().own[budget.pk], 0)
        self.order.set_status(Order.Status.PLACED)
        self.assertEqual(Spending().own[budget.pk], self.order.total)
        self.order.set_status(Order.Status.RECEIVED)
        self.order.set_status(Order.Status.RECEIVED)  # no-op
        self.assertEqual(on_hand(part), before + 20)
        self.assertEqual(part.moves.filter(note__contains=self.order.number).count(), 1)
        with self.assertRaises(ValidationError):
            self.order.set_status(Order.Status.DRAFT)

    def test_empty_order_cannot_be_placed(self):
        empty = Order.objects.create(supplier=Vendor.objects.create(name='Nobody'))
        with self.assertRaises(ValidationError):
            empty.set_status(Order.Status.PLACED)

    def test_pages(self):
        for url in (reverse('orders:list'), self.order.get_absolute_url(), reverse('orders:edit', args=[self.order.pk]),
                    reverse('orders:create'), self.order.budget.get_absolute_url()):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertContains(self.client.get(self.order.budget.get_absolute_url()), self.order.number)

    def test_create_with_lines_and_place_from_the_page(self):
        part = Part.objects.get(part_number='DB25-M')
        response = self.client.post(reverse('orders:create'), {
            'supplier': self.acme.pk, 'parent_budget': Budget.objects.get(budget_number='B-OPS').pk,
            'budget': Budget.objects.get(budget_number='B-OPS-WH').pk, 'notes': '',
            'lines-TOTAL_FORMS': '1', 'lines-INITIAL_FORMS': '0', 'lines-MIN_NUM_FORMS': '0', 'lines-MAX_NUM_FORMS': '1000',
            'lines-0-part': part.pk, 'lines-0-quantity': '4', 'lines-0-unit_price': '1.50',
        })
        order = Order.objects.get(supplier=self.acme)
        self.assertRedirects(response, order.get_absolute_url())
        self.assertEqual(order.total, Decimal('6.00'))
        self.client.post(reverse('orders:set_status', args=[order.pk]), {'status': 'placed'})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PLACED)
        # Placed orders can no longer be edited.
        self.assertRedirects(self.client.get(reverse('orders:edit', args=[order.pk])), order.get_absolute_url())

    def test_shipping_documents(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        page = self.client.get(self.order.get_absolute_url())
        self.assertContains(page, 'Shipping documents')
        self.assertContains(page, reverse('core:image_upload', args=['orders.order', self.order.pk]))
        image = BytesIO()
        PILImage.new('RGB', (40, 60), 'white').save(image, 'PNG')
        upload = SimpleUploadedFile('delivery-note.png', image.getvalue(), content_type='image/png')
        self.client.post(reverse('core:image_upload', args=['orders.order', self.order.pk]),
                         {'images': [upload], 'next': self.order.get_absolute_url()})
        self.assertEqual(self.order.images.count(), 1)

    def test_budget_in_two_steps(self):
        ops, wh, eng = (Budget.objects.get(budget_number=n) for n in ('B-OPS', 'B-OPS-WH', 'B-ENG'))
        url = reverse('orders:edit', args=[self.order.pk])
        page = self.client.get(url)
        self.assertEqual(page.context['form'].initial['parent_budget'], ops.pk)  # the demo order is on B-OPS-WH
        self.assertContains(page, f'data-parent="{ops.pk}"')
        lines = self.order.lines.all()
        data = {'supplier': self.order.supplier_id, 'notes': '',
                'lines-TOTAL_FORMS': len(lines), 'lines-INITIAL_FORMS': len(lines)}
        for i, line in enumerate(lines):
            data.update({f'lines-{i}-id': line.pk, f'lines-{i}-order': self.order.pk, f'lines-{i}-part': line.part_id,
                         f'lines-{i}-quantity': line.quantity, f'lines-{i}-unit_price': line.unit_price})
        # A sub-budget must belong to the parent picked.
        resp = self.client.post(url, {**data, 'parent_budget': eng.pk, 'budget': wh.pk})
        self.assertContains(resp, 'is not a sub-budget of')
        # No sub-budget: the parent itself is charged.
        self.assertRedirects(self.client.post(url, {**data, 'parent_budget': ops.pk, 'budget': ''}), self.order.get_absolute_url())
        self.order.refresh_from_db()
        self.assertEqual(self.order.budget, ops)
        self.assertEqual(self.client.get(url).context['form'].initial['parent_budget'], ops.pk)

    def test_receive_from_a_table(self):
        self.order.set_status(Order.Status.PLACED)
        f_line = self.order.lines.get(part__part_number='DB25-F')
        page = self.client.get(reverse('orders:receive', args=[self.order.pk]))
        # The rows carry what the pasted table is matched on.
        self.assertContains(page, f'data-line="{f_line.pk}" data-part="DB25-F" data-qty="20"')
        self.assertContains(page, 'data-code=')
        self.assertContains(page, 'receive_import.js')
        # The AI instructions name the order and its open lines, and the CSV header.
        instructions = page.content.decode().split('id="ai-instructions"')[1].split('</textarea>')[0]
        self.assertIn(self.order.number, instructions)
        self.assertIn('Connector Supply Co.', instructions)
        self.assertIn('Part number,Quantity,Location', instructions)
        self.assertIn('- DB25-F | ', instructions)
        self.assertIn('| 20 ', instructions)

    def test_receive_page_ticks_lines_into_locations(self):
        self.order.set_status(Order.Status.PLACED)
        f_line = self.order.lines.get(part__part_number='DB25-F')
        m_line = self.order.lines.get(part__part_number='DB25-M')
        drawer = Location.objects.get(name='Drawer 4')
        cage = Location.objects.get(name='Cage')
        url = reverse('orders:receive', args=[self.order.pk])
        page = self.client.get(url)
        self.assertContains(page, f'name="receive-{f_line.pk}"')
        self.assertContains(page, 'Set for all lines')
        # Nothing ticked: nothing happens.
        self.client.post(url, {f'location-{f_line.pk}': cage.pk})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PLACED)
        # Only the socket came: into the cage; the plug stays open.
        before = StockItem.objects.filter(part=f_line.part, location=cage).first()
        self.client.post(url, {f'receive-{f_line.pk}': '1', f'location-{f_line.pk}': cage.pk, f'location-{m_line.pk}': drawer.pk})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PARTIAL)
        self.assertEqual(StockItem.objects.get(part=f_line.part, location=cage).quantity, (before.quantity if before else 0) + 20)
        f_line.refresh_from_db()
        self.assertEqual((f_line.location, bool(f_line.received_at)), (cage, True))
        self.assertTrue(self.order.counts_against_budget)
        page = self.client.get(url)
        self.assertNotContains(page, f'name="receive-{f_line.pk}"')  # already in
        # Then the rest.
        self.client.post(url, {f'receive-{m_line.pk}': '1', f'location-{m_line.pk}': drawer.pk})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.RECEIVED)
        self.assertEqual(StockItem.objects.get(part=m_line.part, location=drawer).quantity, 30 + 10)
        self.assertEqual(self.client.get(url).status_code, 302)  # nothing left to receive

    def test_parts_are_added_from_the_picker_or_made_on_the_fly(self):
        search = self.client.get(reverse('inventory:part_search') + '?q=d-sub').json()['parts']
        self.assertIn('DB25-M', [p['part_number'] for p in search])
        quick = self.client.post(reverse('inventory:part_quick_create'), {'part_number': 'NEW-CONN-9', 'name': '9-way connector'},
                                 content_type='application/json')
        new_part = Part.objects.get(pk=quick.json()['part']['id'])
        self.assertEqual(new_part.name, '9-way connector')
        dup = self.client.post(reverse('inventory:part_quick_create'), {'part_number': 'new-conn-9'}, content_type='application/json')
        self.assertEqual(dup.status_code, 400)
        page = self.client.get(reverse('orders:create'))
        self.assertContains(page, 'data-part-picker')
        self.assertContains(page, 'Add to order')
        response = self.client.post(reverse('orders:create'), {
            'supplier': self.acme.pk, 'budget': '', 'notes': '',
            'lines-TOTAL_FORMS': '2', 'lines-INITIAL_FORMS': '0', 'lines-MIN_NUM_FORMS': '0', 'lines-MAX_NUM_FORMS': '1000',
            'lines-0-part': new_part.pk, 'lines-0-quantity': '3', 'lines-0-unit_price': '',
            'lines-1-part': '', 'lines-1-quantity': '', 'lines-1-unit_price': '',  # a removed row
        })
        order = Order.objects.get(supplier=self.acme)
        self.assertRedirects(response, order.get_absolute_url())
        self.assertEqual(list(order.lines.values_list('part__part_number', 'unit_price')), [('NEW-CONN-9', Decimal('0'))])
        edit = self.client.get(reverse('orders:edit', args=[order.pk]))
        self.assertContains(edit, 'NEW-CONN-9')
