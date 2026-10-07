from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from assemblies.models import Assembly

from .models import Connector, Device, PinMap, Signal, TagOption


class DeviceTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        self.client.login(username='admin', password='admin')
        self.pdu = Device.objects.get(part_number='PDU-100')


class DeviceTests(DeviceTestCase):
    def test_definition_uses_harness_format(self):
        d = self.pdu.definition()
        self.assertEqual([c['id'] for c in d['connectors']], ['J01', 'J02'])
        self.assertEqual(d['connectors'][0]['pins'][1],
                         {'id': '2', 'label': '13', 'signal': 'GND', 'tags': ['PWR_IN-', '', '', ''], 'set': None, 'set_type': ''})
        self.assertEqual(self.pdu.connectors.get(designator='J01').mating_designator, 'P01')

    def test_versions_only_on_change_and_can_be_restored(self):
        self.assertEqual(self.pdu.version, 1)
        self.assertFalse(self.pdu.snapshot())  # nothing changed
        j02 = self.pdu.connectors.get(designator='J02')
        j02.pins.filter(label='3').update(signal='SHIELD')
        self.assertTrue(self.pdu.snapshot())
        self.assertEqual(self.pdu.version, 2)
        self.pdu.activate_version(1)
        self.assertEqual(self.pdu.version, 1)
        self.assertEqual(self.pdu.connectors.get(designator='J02').pins.get(label='3').signal, 'GND')
        # The physical connector part survives restoring a version.
        self.assertEqual(self.pdu.connectors.get(designator='J01').part.part_number, 'DB25-F')

    def test_connector_editor_adds_renumbers_and_removes_pins(self):
        for name in ('RX', 'TX'):
            Signal.objects.get_or_create(name=name)
        url = reverse('devices:connector_create', args=[self.pdu.pk])
        resp = self.client.post(url, {
            'designator': 'j03', 'side': 'right', 'part': '', 'description': 'Aux',
            'pins-TOTAL_FORMS': 3, 'pins-INITIAL_FORMS': 0, 'pins-MIN_NUM_FORMS': 0, 'pins-MAX_NUM_FORMS': 1000,
            'pins-0-label': '1', 'pins-1-label': '2', 'pins-2-label': '3',
        })
        j03 = self.pdu.connectors.get(designator='J03')  # upper-cased
        self.assertRedirects(resp, self.pdu.get_absolute_url() + f'?connector={j03.pk}')
        self.assertEqual(list(j03.pins.values_list('label', flat=True)), ['1', '2', '3'])
        self.pdu.refresh_from_db()
        self.assertEqual(self.pdu.version, 2)

        pins = list(j03.pins.all())
        data = {
            'designator': 'J03', 'side': 'right', 'part': '', 'description': 'Aux',
            'pins-TOTAL_FORMS': 3, 'pins-INITIAL_FORMS': 3, 'pins-MIN_NUM_FORMS': 0, 'pins-MAX_NUM_FORMS': 1000,
        }
        for i, (pin, signal) in enumerate(zip(pins, ['RX', 'TX', 'GND'])):
            data.update({f'pins-{i}-id': pin.pk, f'pins-{i}-connector': j03.pk, f'pins-{i}-label': pin.label,
                         f'pins-{i}-signal': signal})
        data['pins-0-DELETE'] = 'on'
        self.client.post(reverse('devices:connector_edit', args=[self.pdu.pk, j03.pk]), data)
        self.assertEqual(list(j03.pins.values_list('position', 'signal')), [(1, 'TX'), (2, 'GND')])

    def test_add_pin_rows_one_at_a_time(self):
        j02 = self.pdu.connectors.get(designator='J02')
        url = reverse('devices:connector_edit', args=[self.pdu.pk, j02.pk])
        self.assertContains(self.client.get(url), 'id="btn-add-pin"')
        pins = list(j02.pins.all())
        data = {
            'designator': 'J02', 'side': j02.side, 'part': j02.part_id or '', 'description': j02.description,
            'pins-TOTAL_FORMS': len(pins) + 2, 'pins-INITIAL_FORMS': len(pins),
            'pins-MIN_NUM_FORMS': 0, 'pins-MAX_NUM_FORMS': 1000,
        }
        for i, pin in enumerate(pins):
            data.update({f'pins-{i}-id': pin.pk, f'pins-{i}-connector': j02.pk,
                         f'pins-{i}-label': pin.label, f'pins-{i}-signal': pin.signal})
        n = len(pins)
        Signal.objects.get_or_create(name='SHIELD')
        data.update({f'pins-{n}-label': '4', f'pins-{n}-signal': 'SHIELD',       # added with "+ Add pin"
                     f'pins-{n + 1}-label': '', f'pins-{n + 1}-signal': ''})    # blank new row: ignored
        self.client.post(url, data)
        self.assertEqual(list(j02.pins.values_list('position', 'label', 'signal'))[-1], (4, '4', 'SHIELD'))
        self.assertEqual(j02.pins.count(), 4)

    def test_duplicate_designator_rejected(self):
        resp = self.client.post(reverse('devices:connector_create', args=[self.pdu.pk]), {
            'designator': 'J01', 'side': 'left', 'part': '', 'description': '',
            'pins-TOTAL_FORMS': 0, 'pins-INITIAL_FORMS': 0, 'pins-MIN_NUM_FORMS': 0, 'pins-MAX_NUM_FORMS': 1000,
        })
        self.assertContains(resp, 'already has a J01')

    def test_external_device_has_no_assembly(self):
        assembly = Assembly.objects.create(name='Inverter', assembly_type=Assembly.Type.DEVICE)
        resp = self.client.post(reverse('devices:create'), {
            'name': 'Inverter', 'origin': 'external', 'role': 'other', 'assembly': assembly.pk, 'color': '#3b7dd8',
        })
        self.assertContains(resp, 'External devices are not built by us')

    def test_assembly_of_type_device_gets_connectors(self):
        assembly = Assembly.objects.create(name='Inverter 3kW', assembly_type=Assembly.Type.DEVICE)
        resp = self.client.get(assembly.get_absolute_url())
        self.assertContains(resp, 'Define connectors')
        resp = self.client.post(reverse('devices:from_assembly', args=[assembly.pk]))
        device = Device.objects.get(assembly=assembly)
        self.assertRedirects(resp, device.get_absolute_url())
        self.assertContains(self.client.get(self.pdu.assembly.get_absolute_url()), 'Power in')

    def test_part_page_shows_connector_use(self):
        resp = self.client.get(Connector.objects.get(device=self.pdu, designator='J01').part.get_absolute_url())
        self.assertContains(resp, 'Used as a connector on')

    def test_device_page_sidebar_and_views(self):
        j01 = self.pdu.connectors.get(designator='J01')
        page = self.client.get(self.pdu.get_absolute_url())
        # Sidebar: details link plus every connector.
        self.assertContains(page, 'Device details')
        self.assertContains(page, f'?connector={j01.pk}"')
        self.assertContains(page, f'?connector={self.pdu.connectors.get(designator="J02").pk}"')
        # By default the right side shows the details (owner, role, versions, harness projects).
        self.assertContains(page, 'Product (unit we build)')
        self.assertContains(page, 'Carla Rossi')
        self.assertContains(page, 'Harness projects')
        # Clicking a connector shows its pins instead.
        page = self.client.get(self.pdu.get_absolute_url() + f'?connector={j01.pk}')
        self.assertContains(page, 'Edit pins')
        self.assertContains(page, '<td>13</td>')
        self.assertContains(page, '<td>GND</td>')
        self.assertContains(page, '<td><code>PWR_IN-</code></td>')
        self.assertContains(page, 'DB25-F')
        self.assertNotContains(page, 'Versions</h2>')
        # Another device's connector id isn't shown here.
        other = Connector.objects.exclude(device=self.pdu).first()
        self.assertContains(self.client.get(self.pdu.get_absolute_url() + f'?connector={other.pk}'), 'Device details</h2>')

    def test_saving_pins_returns_to_that_connector(self):
        j02 = self.pdu.connectors.get(designator='J02')
        pins = list(j02.pins.all())
        data = {'designator': 'J02', 'side': j02.side, 'part': j02.part_id or '', 'description': j02.description,
                'pins-TOTAL_FORMS': len(pins), 'pins-INITIAL_FORMS': len(pins),
                'pins-MIN_NUM_FORMS': 0, 'pins-MAX_NUM_FORMS': 1000}
        for i, pin in enumerate(pins):
            data.update({f'pins-{i}-id': pin.pk, f'pins-{i}-connector': j02.pk, f'pins-{i}-label': pin.label, f'pins-{i}-signal': pin.signal})
        resp = self.client.post(reverse('devices:connector_edit', args=[self.pdu.pk, j02.pk]), data)
        self.assertRedirects(resp, self.pdu.get_absolute_url() + f'?connector={j02.pk}')

    def test_pages_render(self):
        for url in [
            reverse('devices:list'), reverse('devices:list') + '?origin=external&role=power_supply&q=psu',
            reverse('devices:create'), reverse('devices:create') + '?origin=external',
            self.pdu.get_absolute_url(), reverse('devices:edit', args=[self.pdu.pk]),
            reverse('devices:connector_create', args=[self.pdu.pk]),
            reverse('devices:connector_edit', args=[self.pdu.pk, self.pdu.connectors.first().pk]),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)


def connector_post(connector, pins, **extra):
    """Form data for the connector editor: pins = [(pin or None, {field: value})]."""
    data = {'designator': connector.designator, 'side': connector.side,
            'part': connector.part_id or '', 'description': connector.description, 'tag_columns': connector.tag_columns,
            'pins-TOTAL_FORMS': len(pins), 'pins-INITIAL_FORMS': sum(1 for p, _ in pins if p),
            'pins-MIN_NUM_FORMS': 0, 'pins-MAX_NUM_FORMS': 1000, **extra}
    for i, (pin, values) in enumerate(pins):
        if pin:
            data.update({f'pins-{i}-id': pin.pk, f'pins-{i}-connector': connector.pk})
            base = {'label': pin.label, 'signal': pin.signal, **pin.tag_fields(), 'set_number': pin.set_number or '', 'set_type': pin.set_type}
        else:
            base = {'label': '', 'signal': '', 'tag1': '', 'tag2': '', 'tag3': '', 'tag4': '', 'set_number': '', 'set_type': ''}
        for k, v in {**base, **values}.items():
            data[f'pins-{i}-{k}'] = v
    return data


class PinDetailsTests(DeviceTestCase):
    def test_four_tag_columns_with_their_own_lists(self):
        j02 = self.pdu.connectors.get(designator='J02')
        pins = list(j02.pins.all())
        url = reverse('devices:connector_edit', args=[self.pdu.pk, j02.pk])
        page = self.client.get(url)
        for column in range(1, 5):
            self.assertContains(page, f'name="pins-0-tag{column}"')
            self.assertContains(page, f'data-column="{column}"')
        self.assertContains(page, '+ Add new…')
        self.assertContains(page, 'class="sort-head" data-col="1">Tag 1</button>')  # the editor sorts too
        self.assertContains(page, 'id="btn-add-tag-col"')
        self.assertContains(page, '<option value="CAN1_H" selected>CAN1_H</option>')  # from the Tag 1 list
        # A value typed with "+ Add new…" is saved and joins its column's list; repeats are fine.
        resp = self.client.post(url, connector_post(j02, [
            (pins[0], {'tag2': 'Bus A', 'tag4': 'Front'}), (pins[1], {'tag2': 'Bus A'}), (pins[2], {'tag3': '__new__'}),
        ]))
        self.assertRedirects(resp, j02.get_absolute_url())
        self.assertEqual(list(j02.pins.values_list('tag2', flat=True)), ['Bus A', 'Bus A', ''])
        self.assertEqual(j02.pins.get(label='3').tag3, '')  # "+ Add new…" without a value is ignored
        self.assertEqual(TagOption.names_for(j02)[2], ['Bus A'])
        self.assertEqual(TagOption.names_for(j02)[4], ['Front'])
        self.assertIn('CAN1_H', TagOption.names_for(j02)[1])
        self.assertEqual(self.pdu.definition()['connectors'][1]['pins'][0]['tags'], ['CAN1_H', 'Bus A', '', 'Front'])
        # Tags are local to the connector: another connector's Tag 2 dropdown doesn't offer it.
        j01 = self.pdu.connectors.get(designator='J01')
        self.assertNotContains(self.client.get(reverse('devices:connector_edit', args=[self.pdu.pk, j01.pk])), 'value="Bus A"')
        self.assertEqual(TagOption.names_for(j01)[2], [])
        # Clones bring their lists along.
        copy = self.pdu.clone_connector(j02)
        self.assertEqual(TagOption.names_for(copy)[2], ['Bus A'])

    def test_sets_function_part_details_and_signal_list(self):
        j02 = self.pdu.connectors.get(designator='J02')
        pins = list(j02.pins.all())
        data = connector_post(j02, [(pins[0], {'set_number': 3, 'set_type': 'twisted'}), (pins[1], {'signal': 'NOT_A_SIGNAL'}), (pins[2], {})],
                              description='CAN bus')
        resp = self.client.post(reverse('devices:connector_edit', args=[self.pdu.pk, j02.pk]), data)
        self.assertContains(resp, 'Select a valid choice')  # signals come from the shared list
        data['pins-1-signal'] = 'GND'
        self.client.post(reverse('devices:connector_edit', args=[self.pdu.pk, j02.pk]), data)
        j02.refresh_from_db()
        self.assertEqual(j02.description, 'CAN bus')
        first = j02.pins.first()
        self.assertEqual((first.set_number, first.set_type), (3, 'twisted'))
        page = self.client.get(j02.get_absolute_url())
        self.assertContains(page, '<dt>Function</dt><dd>CAN bus</dd>')
        self.assertNotContains(page, 'Gender')
        self.assertContains(page, '<td>3</td><td>Twisted</td>')
        # The editor: Function instead of Description, no gender or details, and the parts' details for the chosen part.
        editor = self.client.get(reverse('devices:connector_edit', args=[self.pdu.pk, j02.pk]))
        self.assertContains(editor, '<label for="id_description">Function:</label>')
        self.assertNotContains(editor, 'id_gender')
        self.assertNotContains(editor, 'id_details')
        info = editor.context['part_info'][str(j02.part_id)]
        self.assertEqual((info['part_number'], info['name']), (j02.part.part_number, j02.part.name))
        self.assertContains(editor, 'id="part-info"')
        html = editor.content.decode()
        # The editor's script reads the part data, so it must come first.
        self.assertLess(html.index('id="part-info"'), html.index('<script>'))
        self.assertNotContains(page, '<th class="num">#</th>')
        self.assertNotContains(self.client.get(reverse('devices:connector_edit', args=[self.pdu.pk, j02.pk])), 'add_pins')

    def test_every_pin_column_sorts_both_ways(self):
        j01 = self.pdu.connectors.get(designator='J01')
        j01.pins.filter(label='1').update(tag2='b', set_number=2)
        j01.pins.filter(label='13').update(tag2='a', set_number=10)
        base = j01.get_absolute_url()
        pins = lambda sort: [p.label for p in self.client.get(base + f'&sort={sort}').context['pins']]
        self.assertEqual(pins('tag1'), ['2', '14', '1', '13'])      # PWR_AUX+, PWR_AUX-, PWR_IN+, PWR_IN-
        self.assertEqual(pins('-tag1'), ['13', '1', '14', '2'])
        self.assertEqual(pins('tag2'), ['13', '1', '2', '14'])      # a, b, then the pins without one
        self.assertEqual(pins('-tag2'), ['1', '13', '2', '14'])     # still empty last
        self.assertEqual(pins('set'), ['1', '13', '2', '14'])       # 2 before 10
        self.assertEqual(pins('pin'), ['1', '2', '13', '14'])
        page = self.client.get(base + '&sort=tag2')
        for key in ('pin', 'signal', 'tag1', 'tag2', 'set', 'set_type'):
            self.assertContains(page, f'sort={key}"' if key != 'tag2' else 'sort=-tag2"')
        # Only the tag columns the connector uses are shown (Tag 1 and Tag 2 here), before the signal.
        self.assertNotContains(page, 'sort=tag3"')
        html = page.content.decode()
        self.assertLess(html.index('>Tag 2</a>'), html.index('>Signal</a>'))

    def test_signals_page_renames_everywhere_and_blocks_deleting_used_ones(self):
        from harness.models import SignalRule
        SignalRule.objects.create(signal_a='PWR', signal_b='PWR')
        signals = list(Signal.objects.all())
        data = {'signals-TOTAL_FORMS': len(signals) + 1, 'signals-INITIAL_FORMS': len(signals),
                'signals-MIN_NUM_FORMS': 0, 'signals-MAX_NUM_FORMS': 1000}
        for i, s in enumerate(signals):
            data.update({f'signals-{i}-id': s.pk, f'signals-{i}-name': 'VBUS' if s.name == 'PWR' else s.name, f'signals-{i}-description': ''})
            if s.name == 'GND':
                data[f'signals-{i}-DELETE'] = 'on'
        data[f'signals-{len(signals)}-name'] = 'SPARE_1'
        resp = self.client.post(reverse('devices:signals'), data, follow=True)
        self.assertContains(resp, 'Still used, so not deleted: GND')
        self.assertTrue(Signal.objects.filter(name='VBUS').exists())
        self.assertTrue(Signal.objects.filter(name='SPARE_1').exists())
        self.assertTrue(Signal.objects.filter(name='GND').exists())
        self.assertFalse(self.pdu.connectors.get(designator='J01').pins.filter(signal='PWR').exists())
        self.assertTrue(SignalRule.objects.filter(signal_a='VBUS', signal_b='VBUS').exists())
        self.assertFalse(SignalRule.objects.filter(signal_a='PWR').exists())
        self.assertContains(self.client.get(reverse('devices:list')), reverse('devices:signals'))

    def test_clone_connector(self):
        j02 = self.pdu.connectors.get(designator='J02')
        resp = self.client.post(reverse('devices:connector_clone', args=[self.pdu.pk, j02.pk]))
        copy = self.pdu.connectors.get(designator='J03')
        self.assertRedirects(resp, reverse('devices:connector_edit', args=[self.pdu.pk, copy.pk]))
        self.assertEqual(list(copy.pins.values_list('label', 'signal', 'tag1', 'tag2', 'set_number')),
                         list(j02.pins.values_list('label', 'signal', 'tag1', 'tag2', 'set_number')))
        self.assertEqual(copy.part, j02.part)
        self.pdu.refresh_from_db()
        self.assertEqual(self.pdu.version, 2)

    def test_pinout_images_on_the_connector(self):
        j01 = self.pdu.connectors.get(designator='J01')
        page = self.client.get(j01.get_absolute_url())
        self.assertContains(page, 'Pinout images')
        self.assertContains(page, reverse('core:image_upload', args=['devices.connector', j01.pk]))


class InterconnectTests(DeviceTestCase):
    def test_pin_mapping_and_versions(self):
        adapter = Device.objects.create(name='Adapter', role=Device.Role.INTERCONNECT)
        a = Connector.objects.create(device=adapter, designator='J01', side='left')
        b = Connector.objects.create(device=adapter, designator='J02', side='right')
        for c in (a, b):
            for i in (1, 2):
                c.pins.create(position=i, label=str(i))
        adapter.snapshot()
        url = reverse('devices:pin_mapping', args=[adapter.pk])
        self.client.post(url, {'by_position': '1', 'from_connector': a.pk, 'to_connector': b.pk})
        self.assertEqual(adapter.pin_maps.count(), 2)
        self.assertEqual(adapter.definition()['pin_map'], [['J01', '1', 'J02', '1'], ['J01', '2', 'J02', '2']])
        # Cross over: J01.1 -> J02.2, J01.2 -> J02.1
        pa, pb = list(a.pins.all()), list(b.pins.all())
        self.client.post(url, {'from': [pa[0].pk, pa[1].pk], 'to': [pb[1].pk, pb[0].pk]})
        adapter.refresh_from_db()
        self.assertEqual(adapter.definition()['pin_map'], [['J01', '1', 'J02', '2'], ['J01', '2', 'J02', '1']])
        adapter.activate_version(adapter.version - 1)  # restoring a version restores its mapping
        self.assertEqual(adapter.definition()['pin_map'], [['J01', '1', 'J02', '1'], ['J01', '2', 'J02', '2']])
        self.assertContains(self.client.get(adapter.get_absolute_url()), 'Pin mapping')
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_extension_copies_the_pinout(self):
        j02 = self.pdu.connectors.get(designator='J02')
        ext = Device.make_extension(self.pdu, j02)
        self.assertTrue(ext.is_interconnect)
        inp, out = ext.connectors.get(designator='J01'), ext.connectors.get(designator='J02')
        self.assertEqual(out.part, j02.part)
        self.assertEqual(list(out.pins.values_list('label', 'signal', 'tag1')), list(j02.pins.values_list('label', 'signal', 'tag1')))
        self.assertEqual(ext.pin_maps.count(), j02.pins.count())
        self.assertEqual(ext.version, 1)


class TagColumnTests(DeviceTestCase):
    def test_connectors_start_with_one_tag_column_and_can_add_more(self):
        url = reverse('devices:connector_create', args=[self.pdu.pk])
        page = self.client.get(url)
        self.assertContains(page, '<input type="hidden" name="tag_columns" value="1"')
        self.assertContains(page, 'class="tag-col-2" hidden')
        # Saved with two columns and a Tag 2 value.
        self.client.post(url, {
            'designator': 'J05', 'side': 'right', 'part': '', 'description': '', 'tag_columns': 2,
            'pins-TOTAL_FORMS': 1, 'pins-INITIAL_FORMS': 0, 'pins-MIN_NUM_FORMS': 0, 'pins-MAX_NUM_FORMS': 1000,
            'pins-0-label': '1', 'pins-0-tag2': 'Left', 'pins-0-signal': 'TxP',
        })
        j05 = self.pdu.connectors.get(designator='J05')
        self.assertEqual((j05.tag_columns, j05.tag_column_count(), j05.pins.get().tag2, j05.pins.get().signal), (2, 2, 'Left', 'TxP'))
        page = self.client.get(j05.get_absolute_url())
        self.assertContains(page, '>Tag 2</a>')
        self.assertNotContains(page, '>Tag 3</a>')
        # A column that's used never disappears, even if the count says fewer.
        j05.tag_columns = 1
        j05.save()
        self.assertEqual(j05.tag_column_count(), 2)
        # Clones and versions keep the columns.
        copy = self.pdu.clone_connector(j05)
        self.assertEqual(copy.tag_columns, 2)
        self.assertEqual(self.pdu.definition()['connectors'][-1]['tag_columns'], 2)

    def test_basic_signals_are_there(self):
        names = set(Signal.objects.values_list('name', flat=True))
        for name in ('GND', 'PWR', 'TxP', 'TxN', 'RxP', 'RxN', 'CAN_H', 'SHIELD', 'PE', 'NC'):
            self.assertIn(name, names)


class SetTypeTests(DeviceTestCase):
    def test_one_type_per_set(self):
        j02 = self.pdu.connectors.get(designator='J02')
        pins = list(j02.pins.all())
        url = reverse('devices:connector_edit', args=[self.pdu.pk, j02.pk])
        # Set 1 is pins 1 and 2 (twisted shielded); giving pin 1 another type alone is refused.
        resp = self.client.post(url, connector_post(j02, [(pins[0], {'set_type': 'twisted'}), (pins[1], {}), (pins[2], {})]))
        self.assertContains(resp, 'pins in one set share a type')
        # Both changed together is fine.
        self.client.post(url, connector_post(j02, [(pins[0], {'set_type': 'twisted'}), (pins[1], {'set_type': 'twisted'}), (pins[2], {})]))
        self.assertEqual(set(j02.pins.filter(set_number=1).values_list('set_type', flat=True)), {'twisted'})


class PinImportTests(DeviceTestCase):
    def post(self, connector, text, mode='update'):
        from django.core.files.uploadedfile import SimpleUploadedFile
        upload = SimpleUploadedFile('pins.csv', text.encode('utf-8'), content_type='text/csv')
        url = reverse('devices:connector_import', args=[self.pdu.pk, connector.pk])
        return self.client.post(url, {'file': upload, 'mode': mode}, follow=True)

    def test_update_by_label_and_add_new_pins(self):
        j02 = self.pdu.connectors.get(designator='J02')  # pins 1, 2, 3 with tags CAN1_H, CAN1_L, CAN1_GND
        csv_text = 'Pin;Signal;Tag 2;Set;Set type\n1;CAN_H;Bus A;4;twisted pair\n2;CAN_L;Bus A;4;\n9;NEW_SIG;;;\n'
        resp = self.post(j02, csv_text)
        self.assertContains(resp, 'Imported 3 pins into J02: 2 updated, 1 added.')
        self.assertContains(resp, 'New signals added to Devices › Signals: NEW_SIG')
        pins = {p.label: p for p in j02.pins.all()}
        self.assertEqual((pins['1'].tag1, pins['1'].tag2, pins['1'].set_number, pins['1'].set_type), ('CAN1_H', 'Bus A', 4, 'twisted'))
        self.assertEqual(pins['2'].set_type, 'twisted')  # one type per set
        self.assertEqual(pins['3'].tag1, 'CAN1_GND')      # not in the file: kept
        self.assertEqual((pins['9'].position, pins['9'].signal), (4, 'NEW_SIG'))
        self.assertTrue(Signal.objects.filter(name='NEW_SIG').exists())
        j02.refresh_from_db()
        self.assertEqual(j02.tag_columns, 2)
        self.assertIn('Bus A', TagOption.names_for(j02)[2])
        self.pdu.refresh_from_db()
        self.assertEqual(self.pdu.version, 2)

    def test_replace_the_whole_table(self):
        j02 = self.pdu.connectors.get(designator='J02')
        resp = self.post(j02, 'pin,tag,signal\nS,SHIELD,SHIELD\n2,CAN1_L,can_l\n', mode='replace')
        self.assertContains(resp, '1 updated, 1 added, 2 removed')
        self.assertEqual(list(j02.pins.values_list('position', 'label', 'signal')), [(1, 'S', 'SHIELD'), (2, '2', 'CAN_L')])

    def test_problems_are_reported_and_bad_files_change_nothing(self):
        j02 = self.pdu.connectors.get(designator='J02')
        before = list(j02.pins.values_list('label', 'tag1', 'signal'))
        for text, message in (
            ('Signal,Tag\nGND,x\n', 'with one called &quot;Pin&quot;'),
            ('Pin\n1\n1\n', 'Pin 1 is in the file twice'),
            ('', 'The file is empty'),
        ):
            with self.subTest(text=text):
                self.assertContains(self.post(j02, text), message)
        self.assertEqual(list(j02.pins.values_list('label', 'tag1', 'signal')), before)
        resp = self.post(j02, 'Pin,Set,Set type,Colour\n1,x,braided,red\n,,,\n')
        self.assertContains(resp, 'set &quot;x&quot; is not a whole number')
        self.assertContains(resp, 'set type &quot;braided&quot; not known')
        self.assertContains(resp, 'Columns not used: Colour')

    def test_import_form_on_existing_connectors_only(self):
        j02 = self.pdu.connectors.get(designator='J02')
        self.assertContains(self.client.get(reverse('devices:connector_edit', args=[self.pdu.pk, j02.pk])), 'Import a pin table')
        self.assertContains(self.client.get(reverse('devices:connector_create', args=[self.pdu.pk])), 'save the connector first')


class DeviceDeleteTests(DeviceTestCase):
    def test_removed_only_when_no_harness_uses_it(self):
        from harness.models import HarnessProject
        project = HarnessProject()
        project.save_version({'name': 'Bench', 'instances': [{'instance_id': 'i1', 'device_id': str(self.pdu.pk)}], 'harnesses': []})
        url = reverse('devices:delete', args=[self.pdu.pk])
        page = self.client.get(url)
        self.assertContains(page, "can't be removed")
        self.assertContains(page, 'Bench')
        self.client.post(url)
        self.assertTrue(Device.objects.filter(pk=self.pdu.pk).exists())
        project.save_version({'name': 'Bench', 'instances': [], 'harnesses': []})
        self.assertContains(self.client.get(url), 'Remove device')
        self.assertRedirects(self.client.post(url), reverse('devices:list'))
        self.assertFalse(Device.objects.filter(pk=self.pdu.pk).exists())
