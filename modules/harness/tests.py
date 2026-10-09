import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import Client, TestCase
from django.urls import reverse

from devices.models import Device
from users.models import User

from .models import HarnessProject, SignalRule


class HarnessTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        self.client.login(username='admin', password='admin')
        self.api = reverse('harness:api_devices').removesuffix('/devices')
        self.pdu = Device.objects.get(part_number='PDU-100')

    def post_json(self, path, data):
        return self.client.post(self.api + path, json.dumps(data), content_type='application/json')


class HarnessApiTests(HarnessTestCase):
    def test_device_library(self):
        devices = self.client.get(self.api + '/devices').json()
        pdu = next(d for d in devices if d['id'] == str(self.pdu.pk))
        self.assertEqual(pdu['version'], 1)
        self.assertEqual([c['id'] for c in pdu['connectors']], ['J01', 'J02'])
        self.assertIn({'id': '1', 'label': '1', 'signal': 'PWR', 'tags': ['PWR_IN+', '', '', ''], 'set': None, 'set_type': ''}, pdu['connectors'][0]['pins'])

    def test_harness_cannot_create_or_change_devices(self):
        before = (Device.objects.count(), self.pdu.version, list(self.pdu.connectors.values_list('designator', flat=True)))
        new = {'name': 'Scope', 'part_number': '', 'color': '#123456', 'responsible_user_id': None, 'origin': 'external',
               'connectors': [{'id': 'CH1', 'side': 'left', 'pins': [{'id': '1', 'label': 'SIG', 'signal': 'SENSE'}]}]}
        self.assertEqual(self.post_json('/devices', new).status_code, 405)
        edited = self.client.get(f'{self.api}/devices/{self.pdu.pk}').json()
        edited['connectors'] = []
        self.assertEqual(self.post_json('/devices', edited).status_code, 405)
        self.assertEqual(self.post_json(f'/devices/{self.pdu.pk}', edited).status_code, 405)
        self.assertEqual(self.client.post(f'{self.api}/devices/{self.pdu.pk}/versions/1/activate').status_code, 404)
        self.pdu.refresh_from_db()
        after = (Device.objects.count(), self.pdu.version, list(self.pdu.connectors.values_list('designator', flat=True)))
        self.assertEqual(before, after)

    def test_device_read_includes_old_versions(self):
        self.pdu.connectors.get(designator='J02').pins.filter(label='3').update(signal='SHIELD')
        self.pdu.snapshot()
        old = self.client.get(f'{self.api}/devices/{self.pdu.pk}?version=1').json()
        self.assertEqual(old['connectors'][1]['pins'][2]['signal'], 'GND')
        self.assertEqual(self.client.get(f'{self.api}/devices/{self.pdu.pk}').json()['version'], 2)

    def test_project_versions(self):
        psu = Device.objects.get(name='Bench power supply')
        project = {'name': 'PDU test bench', 'instances': [
            {'instance_id': 'a', 'device_id': str(psu.pk), 'device_version': 1, 'x': 0, 'y': 0, 'label': ''},
            {'instance_id': 'b', 'device_id': str(self.pdu.pk), 'device_version': 1, 'x': 400, 'y': 0, 'label': 'UUT'},
        ], 'harnesses': [{'id': 'h', 'label': 'H1', 'from_instance': 'a', 'from_connector': 'J01',
                          'from_harness_connector': 'P01', 'branches': [{'id': 'b1', 'to_instance': 'b', 'to_connector': 'J01',
                          'to_harness_connector': 'P01', 'connections': [
                              {'id': 'w1', 'from_pin': '1', 'to_pin': '1', 'signal': 'PWR', 'verified': True},
                              {'id': 'w2', 'from_pin': '2', 'to_pin': '2', 'signal': 'GND', 'verified': False}]}]}]}
        v1 = self.post_json('/projects', project).json()
        self.assertEqual(v1['version'], 1)
        v2 = self.post_json('/projects', {**v1, 'name': 'PDU test bench (rev B)'}).json()
        self.assertEqual((v2['id'], v2['version']), (v1['id'], 2))
        p = HarnessProject.objects.get(pk=v1['id'])
        self.assertEqual(p.stats(), {'devices': 2, 'harnesses': 1, 'wires': 2, 'verified': 1})

        self.client.post(f'{self.api}/projects/{p.pk}/versions/1/activate')
        p.refresh_from_db()
        self.assertEqual((p.version, p.name), (1, 'PDU test bench'))
        # The device page lists the project, and the list page shows it.
        self.assertContains(self.client.get(self.pdu.get_absolute_url()), 'PDU test bench')
        self.assertContains(self.client.get(reverse('harness:list')), '1 / 2')

        self.client.delete(f'{self.api}/projects/{p.pk}')
        self.assertFalse(HarnessProject.objects.filter(pk=p.pk).exists())

    def test_signal_rules_and_users(self):
        self.post_json('/signal-rules', {'pairs': [['PWR', 'PWR'], ['rx', 'TX'], ['RX', 'tx'], ['', 'GND']]})
        self.assertEqual(self.client.get(self.api + '/signal-rules').json(), {'pairs': [['PWR', 'PWR'], ['rx', 'TX']]})
        users = self.client.get(self.api + '/users').json()['users']
        self.assertIn({'id': str(User.objects.get(username='carla').pk), 'name': 'Carla Rossi'}, users)

    def test_writes_need_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.login(username='admin', password='admin')
        resp = client.post(self.api + '/signal-rules', '{"pairs": []}', content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        page = client.get(reverse('harness:designer'))
        token = page.context['csrf_token']
        resp = client.post(self.api + '/signal-rules', '{"pairs": []}', content_type='application/json',
                           headers={'X-CSRFToken': str(token)})
        self.assertEqual(resp.status_code, 200)

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.api + '/devices').status_code, 302)

    def test_designer_page(self):
        resp = self.client.get(reverse('harness:designer'))
        self.assertContains(resp, f'data-api="{self.api}"')
        self.assertContains(resp, 'harness/designer.js')
        self.assertContains(resp, 'id="btn-export-csv"')
        # No device editor in the designer: new devices are made in the Devices module.
        self.assertNotContains(resp, 'device-modal')
        self.assertNotContains(resp, 'btn-save-device')
        self.assertContains(resp, f'id="btn-new-device" href="{reverse("devices:create")}"')


class ImportTests(TestCase):
    def test_import_stand_alone_data(self):
        User.objects.create_user('user1', password='x')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def write(rel, data):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data), encoding='utf-8')

            conn = lambda sig: [{'id': 'J1', 'side': 'right', 'pins': [{'id': '1', 'label': '1', 'signal': sig}]}]
            write('devices/ecu/1.json', {'name': 'ECU', 'part_number': 'E-1', 'color': '#111111', 'connectors': conn('GND')})
            write('devices/ecu/2.json', {'name': 'ECU', 'part_number': 'E-1', 'color': '#111111',
                                         'responsible_user_id': 'u1', 'connectors': conn('PWR')})
            write('devices/ecu/pointer.json', {'current': 1})
            write('projects/p1/1.json', {'name': 'Bench', 'instances': [{'instance_id': 'i', 'device_id': 'ecu'}], 'harnesses': []})
            write('projects/p1/pointer.json', {'current': 1})
            write('settings/signal_rules.json', {'pairs': [['RX', 'TX']]})
            write('settings/users.json', {'users': [{'id': 'u1', 'name': 'user1'}]})
            # Harness alone can't bring in devices: it needs them in Devices first.
            with self.assertRaises(CommandError):
                call_command('import_harness', tmp, stdout=StringIO())
            self.assertFalse(Device.objects.exists())
            call_command('import_harness_devices', tmp, stdout=StringIO())
            call_command('import_harness_devices', tmp, stdout=StringIO())  # safe to repeat
            self.assertEqual(Device.objects.count(), 1)
            call_command('import_harness', tmp, stdout=StringIO())

        ecu = Device.objects.get(name='ECU')
        self.assertEqual(ecu.version, 1)  # pointer said v1
        self.assertEqual(ecu.connectors.get().pins.get().signal, 'GND')
        self.assertEqual(ecu.versions.get(version=2).data['responsible_user_id'], str(User.objects.get(username='user1').pk))
        project = HarnessProject.objects.get(name='Bench')
        self.assertEqual(project.data()['instances'][0]['device_id'], str(ecu.pk))
        self.assertEqual(SignalRule.pairs(), [['RX', 'TX']])


class ExtensionAndOrderTests(HarnessTestCase):
    def test_extension_api(self):
        resp = self.post_json('/extensions', {'device_id': self.pdu.pk, 'connector_id': 'J02'})
        ext = resp.json()
        self.assertEqual(ext['role'], 'interconnect')
        self.assertEqual([c['id'] for c in ext['connectors']], ['J01', 'J02'])
        self.assertEqual(len(ext['pin_map']), 3)
        self.assertEqual([p['tags'][0] for p in ext['connectors'][1]['pins']], ['CAN1_H', 'CAN1_L', 'CAN1_GND'])
        self.assertEqual(self.post_json('/extensions', {'device_id': self.pdu.pk, 'connector_id': 'J99'}).status_code, 404)

    def test_order_parts(self):
        from orders.models import Order
        resp = self.post_json('/order', {'lines': [{'part_number': 'DB25-M', 'quantity': 2}, {'part_number': 'DB25-M', 'quantity': 1},
                                                   {'part_number': 'NOPE-1', 'quantity': 1}], 'note': 'Parts for harness H1'})
        data = resp.json()
        order = Order.objects.get(number=data['number'])
        self.assertEqual(order.status, Order.Status.DRAFT)
        self.assertEqual([(n, int(q)) for n, q in order.lines.values_list('part__part_number', 'quantity')], [('DB25-M', 3)])
        self.assertEqual(data['missing'], ['NOPE-1'])
        self.assertIn('NOPE-1', order.notes)
        self.assertEqual(data['url'], order.get_absolute_url())
        self.assertEqual(self.post_json('/order', {'lines': []}).status_code, 400)
        parts = self.client.get(self.api + '/parts').json()['parts']
        self.assertIn('DB25-M', [p['part_number'] for p in parts])

    def test_designer_page_has_the_new_controls(self):
        page = self.client.get(reverse('harness:designer'))
        for text in ('id="connector-tip"', 'id="btn-create-extension"', 'id="btn-loopback-pending"', 'id="btn-order-parts"',
                     'id="wire-colors"', '>Set</th>', '>Color</th>'):
            self.assertContains(page, text)
        self.assertNotContains(page, 'Wire type')
