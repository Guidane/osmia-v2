from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from inventory.models import Part

from .locations import clean_levels, generate, labels
from .models import Location, PartStock, StockItem, StockMove, on_hand


class StockTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        self.client.login(username='admin', password='admin')

    def hall(self, levels):
        hall = Location.objects.create(name='Hall', label='H')
        generate(hall, clean_levels(levels))
        return hall


class StockLevelTests(StockTestCase):
    def test_demo_stock_is_per_location_with_settings(self):
        bolt = Part.objects.get(part_number='SCR-M3X8')
        item = StockItem.objects.get(part=bolt)
        self.assertEqual((item.quantity, item.location.full_path()), (Decimal('500'), 'Warehouse > Aisle 1 > Bin A1'))
        self.assertEqual((bolt.stock.reorder_level, bolt.stock.average_cost), (Decimal('200'), Decimal('0.12')))

    def test_receive_issue_and_count_per_location(self):
        part = Part.objects.get(part_number='ESD-STRAP')
        hall = self.hall([{'name': 'Shelf', 'count': 2, 'style': 'A'}])
        a, b = Location.objects.get(code='HA'), Location.objects.get(code='HB')
        before = on_hand(part)
        # Receive at a price: the average cost moves.
        StockMove.record(part, StockMove.Type.IN, 12, location=a, unit_cost='4.50')
        self.assertEqual(on_hand(part), before + 12)
        self.assertEqual(PartStock.of(part).average_cost.quantize(Decimal('0.01')), Decimal('4.00'))  # (12×3.50 + 12×4.50) / 24
        # Issues come from a location and can't take more than is there.
        with self.assertRaises(ValidationError):
            StockMove.record(part, StockMove.Type.OUT, -13, location=a)
        move = StockMove.record(part, StockMove.Type.OUT, -2, location=a)
        self.assertEqual(move.unit_cost, Decimal('4.00'))  # booked at the average cost
        # A count sets what's at one location.
        url = reverse('stock:move_create')
        self.client.post(url, {'part': part.pk, 'move_type': 'adjust', 'location': b.pk, 'quantity': '7'})
        self.assertEqual(StockItem.objects.get(part=part, location=b).quantity, 7)
        self.assertEqual(StockItem.objects.get(part=part, location=a).quantity, 10)
        # The issue form checks the chosen location.
        resp = self.client.post(url, {'part': part.pk, 'move_type': 'out', 'location': b.pk, 'quantity': '8'})
        self.assertContains(resp, 'Only 7 pcs at')
        self.assertTrue(hall.pk)

    def test_low_stock_event_and_list(self):
        from core import automation
        seen = []
        listener = lambda key, obj, source: seen.append((key, getattr(obj, 'part_number', '')))  # noqa: E731
        automation.subscribe(listener)
        self.addCleanup(automation._listeners.remove, listener)
        nut = Part.objects.get(part_number='NUT-M3')  # 150 on hand, reorder at 200: already low
        self.assertIn('NUT-M3', self.client.get(reverse('stock:list') + '?low=1').content.decode())
        bolt = Part.objects.get(part_number='SCR-M3X8')  # 500, reorder at 200
        item = StockItem.objects.get(part=bolt)
        StockMove.record(bolt, StockMove.Type.OUT, -300, location=item.location)
        self.assertIn(('stock.stock_low', 'SCR-M3X8'), seen)
        self.assertTrue(nut.pk)

    def test_pages(self):
        part = Part.objects.get(part_number='PSU-24V-150W')
        for url in (reverse('stock:list'), reverse('stock:list') + '?q=a1&location=none&low=1', reverse('stock:move_list'),
                    reverse('stock:move_list') + f'?part={part.pk}&type=out', reverse('stock:move_create') + f'?part={part.pk}',
                    reverse('stock:location_list'), reverse('stock:location_create'), reverse('stock:location_generate'),
                    reverse('stock:part_settings', args=[part.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        page = self.client.get(part.get_absolute_url())
        self.assertContains(page, 'on hand')        # the stock panel on the part's page
        self.assertContains(page, 'Replacement power supply')
        self.client.post(reverse('stock:part_settings', args=[part.pk]), {'reorder_level': '5'})
        self.assertEqual(PartStock.of(part).reorder_level, 5)


class LocationCodeTests(StockTestCase):
    def test_labels(self):
        self.assertEqual(labels('A', 3), ['A', 'B', 'C'])
        self.assertEqual(labels('a', 2, 'y'), ['y', 'z'])
        self.assertEqual(labels('A', 3, 'Z'), ['Z', 'AA', 'AB'])
        self.assertEqual(labels('1', 3, '5'), ['5', '6', '7'])
        self.assertEqual(labels('01', 3), ['01', '02', '03'])
        self.assertEqual(labels('01', 2, '99'), ['099', '100'])

    def test_code_joins_labels_and_follows_changes(self):
        hall = Location.objects.create(name='Hall', label='H')
        row = Location.objects.create(name='Rack row A', label='A', parent=hall)
        shelf = Location.objects.create(name='Shelf 1', label='1', parent=row)
        self.assertEqual(shelf.code, 'HA1')
        self.assertEqual(str(shelf), 'Hall > Rack row A > Shelf 1 (HA1)')
        hall.label, hall.name = 'W', 'Warehouse 2'
        hall.save()
        shelf.refresh_from_db()
        self.assertEqual((shelf.code, shelf.path), ('WA1', 'Warehouse 2 > Rack row A > Shelf 1'))

    def test_generate_rows_racks_shelves(self):
        hall = Location.objects.create(name='Hall')
        data = {'parent': hall.pk}
        for i, (name, count, style) in enumerate([('Rack row', 4, 'A'), ('Rack', 6, '1'), ('Shelf', 4, 'A')]):
            data.update({f'level-{i}-name': name, f'level-{i}-count': count, f'level-{i}-style': style, f'level-{i}-start': ''})
        response = self.client.post(reverse('stock:location_generate'), data)
        self.assertRedirects(response, hall.get_absolute_url())
        self.assertEqual(Location.objects.filter(pk__in=hall.descendant_ids()).count(), 4 + 24 + 96)
        shelf = Location.objects.get(code='A1A')
        self.assertEqual((shelf.name, shelf.path), ('Shelf A', 'Hall > Rack row A > Rack 1 > Shelf A'))
        self.assertEqual(sorted(Location.objects.get(code='B3').children.values_list('code', flat=True)), ['B3A', 'B3B', 'B3C', 'B3D'])
        data['level-1-count'] = 7  # again with one more rack per row: only the new ones are added
        self.client.post(reverse('stock:location_generate'), data)
        self.assertEqual(Location.objects.filter(pk__in=hall.descendant_ids()).count(), 4 + 28 + 112)
        # Stock can be found by its location's code.
        part = Part.objects.get(part_number='ESD-STRAP')
        StockMove.record(part, StockMove.Type.IN, 3, location=shelf)
        self.assertContains(self.client.get(reverse('stock:list') + '?q=a1a'), 'ESD-STRAP')
        for url in (reverse('stock:location_list'), shelf.get_absolute_url()):
            self.assertContains(self.client.get(url), 'A1A')

    def test_bad_input_and_limits(self):
        before = Location.objects.count()
        url = reverse('stock:location_generate')
        for data in (
            {'level-0-name': 'Rack', 'level-0-count': 'x', 'level-0-style': '1'},
            {'level-0-name': '', 'level-0-count': '3', 'level-0-style': '1'},
            {'level-0-name': 'Rack', 'level-0-count': '3', 'level-0-style': 'A', 'level-0-start': '7'},
            {'level-0-name': 'Row', 'level-0-count': '100', 'level-0-style': 'A',
             'level-1-name': 'Rack', 'level-1-count': '100', 'level-1-style': '1'},
            {},
        ):
            with self.subTest(data=data):
                self.assertEqual(self.client.post(url, data).status_code, 200)
        self.assertEqual(Location.objects.count(), before)

    def test_sibling_labels_are_unique(self):
        row = Location.objects.create(name='Row', label='R')
        Location.objects.create(name='Rack 1', label='1', parent=row)
        response = self.client.post(reverse('stock:location_create'), {'name': 'Other', 'label': '1', 'parent': row.pk})
        self.assertContains(response, 'already has the label')


class LocationDeleteTests(StockTestCase):
    def setUp(self):
        super().setUp()
        self.hall_loc = self.hall([{'name': 'Row', 'count': 2, 'style': 'A'}, {'name': 'Shelf', 'count': 3, 'style': '1'}])
        self.shelf = Location.objects.get(code='HB2')

    def test_refused_while_stock_is_kept_there_or_below(self):
        part = Part.objects.first()
        StockMove.record(part, StockMove.Type.IN, 2, location=self.shelf)
        for loc in (self.hall_loc, self.shelf.parent, self.shelf):
            url = reverse('stock:location_delete', args=[loc.pk])
            page = self.client.get(url)
            self.assertContains(page, "can't be deleted")
            self.assertContains(page, part.part_number)
            self.client.post(url)
        self.assertEqual(Location.objects.filter(pk__in={self.hall_loc.pk, *self.hall_loc.descendant_ids()}).count(), 1 + 2 + 6)

    def test_empty_location_is_deleted_with_its_sub_locations(self):
        empty_row = Location.objects.get(code='HA')
        response = self.client.post(reverse('stock:location_delete', args=[empty_row.pk]))
        self.assertRedirects(response, self.hall_loc.get_absolute_url())
        self.assertFalse(Location.objects.filter(code__startswith='HA').exists())
        self.assertContains(self.client.get(reverse('stock:location_delete', args=[self.hall_loc.pk])), '4 sub-locations')
        self.client.post(reverse('stock:location_delete', args=[self.hall_loc.pk]))
        self.assertFalse(Location.objects.filter(code__startswith='H').exists())

    def test_tree_totals_and_delete_back_to_the_tree(self):
        a, b = list(Part.objects.all()[:2])
        StockMove.record(a, StockMove.Type.IN, 5, location=Location.objects.get(code='HA1'))
        StockMove.record(b, StockMove.Type.IN, '2.5', location=Location.objects.get(code='HA2'))
        url = reverse('stock:location_list')
        rows = {n.code: n for n in self.client.get(url).context['object_list'] if n.code.startswith('H')}
        self.assertEqual((rows['H'].part_count, rows['H'].quantity, rows['H'].sub_count), (2, Decimal('7.5'), 8))
        self.assertEqual((rows['HB'].part_count, rows['HB'].quantity, rows['HB'].sub_count), (0, 0, 3))
        response = self.client.post(reverse('stock:location_delete', args=[rows['HB'].pk]), {'next': url})
        self.assertRedirects(response, url)
        self.assertFalse(Location.objects.filter(code__startswith='HB').exists())


class TransferTests(StockTestCase):
    def setUp(self):
        super().setUp()
        self.hall([{'name': 'Shelf', 'count': 3, 'style': 'A'}])
        self.a, self.b = Location.objects.get(code='HA'), Location.objects.get(code='HB')
        self.straps = Part.objects.get(part_number='ESD-STRAP')
        self.bolts = Part.objects.get(part_number='SCR-M3X8')

    def test_move_selected_rows_to_another_location(self):
        url = reverse('stock:list')
        page = self.client.get(url)
        self.assertContains(page, 'id="transfer-form"')
        self.assertContains(page, 'class="row-select" name="items"')
        items = [StockItem.objects.get(part=self.straps), StockItem.objects.get(part=self.bolts)]
        totals = {p.pk: on_hand(p) for p in (self.straps, self.bolts)}
        response = self.client.post(reverse('stock:transfer'), {
            'items': [i.pk for i in items], 'location': self.b.pk, 'next': url, 'note': 'Reorganised'})
        self.assertRedirects(response, url)
        for p in (self.straps, self.bolts):
            self.assertEqual(list(StockItem.objects.filter(part=p).values_list('location', flat=True)), [self.b.pk])
            self.assertEqual(on_hand(p), totals[p.pk])
        moves = StockMove.objects.filter(move_type=StockMove.Type.TRANSFER)
        self.assertEqual(moves.count(), 2)
        self.assertEqual(moves.filter(part=self.straps).get().note, 'Reorganised')
        # Moving onto stock of the same part already there adds them together.
        StockMove.record(self.straps, StockMove.Type.IN, 3, location=self.a)
        item_a = StockItem.objects.get(part=self.straps, location=self.a)
        self.client.post(reverse('stock:transfer'), {'items': [item_a.pk], 'location': self.b.pk})
        self.assertEqual(StockItem.objects.get(part=self.straps, location=self.b).quantity, totals[self.straps.pk] + 3)
        self.assertFalse(StockItem.objects.filter(part=self.straps, location=self.a).exists())
        # The location's and the part's pages offer the same.
        self.assertContains(self.client.get(self.b.get_absolute_url()), 'id="transfer-form"')
        page = self.client.get(self.straps.get_absolute_url())
        self.assertContains(page, 'Transfer')
        self.assertContains(page, '<code>HB</code>')

    def test_needs_a_location_and_rows(self):
        item = StockItem.objects.get(part=self.straps)
        self.client.post(reverse('stock:transfer'), {'items': [item.pk]})
        self.client.post(reverse('stock:transfer'), {'location': self.a.pk})
        self.assertFalse(StockMove.objects.filter(move_type=StockMove.Type.TRANSFER).exists())
        self.assertNotContains(self.client.get(reverse('stock:move_create')), 'value="transfer"')
