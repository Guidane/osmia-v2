from io import StringIO

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core import audit
from inventory.models import Part
from orders.models import Order
from tasks.models import Task
from users.models import User

from .models import HiddenHistory, LogEntry


class AuditTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        self.client.login(username='admin', password='admin')
        self.admin = User.objects.get(username='admin')
        LogEntry.objects.all().delete()  # start each test from an empty log

    def entries(self, **filters):
        return LogEntry.objects.filter(**filters).order_by('id')


class ChangeLogTests(AuditTestCase):
    def test_edit_through_a_page_logs_the_fields_that_changed(self):
        task = Task.objects.get(title__startswith='Repair returned PDU')
        self.client.post(reverse('tasks:set_status', args=[task.pk]), {'status': 'in_progress'})
        e = self.entries(module='tasks', object_id=task.pk).get()
        self.assertEqual(e.action, 'changed')
        self.assertEqual(e.user, self.admin)
        self.assertEqual(e.actor_module, 'tasks')
        self.assertEqual(e.triggered_by, '')  # the module changed its own record: no chain tag
        self.assertTrue(e.chain_id)
        self.assertIn({'field': 'status', 'old': 'To do', 'new': 'In progress'}, e.changes)

    def test_saving_without_changes_logs_nothing_and_deletes_are_logged(self):
        task = Task.objects.create(title='Temp')
        task.save()
        self.assertEqual(list(self.entries(object_id=task.pk, module='tasks').values_list('action', flat=True)), ['created'])
        pk = task.pk
        task.delete()
        self.assertEqual(self.entries(object_id=pk, module='tasks').last().action, 'deleted')

    def test_child_rows_are_logged_on_their_parent(self):
        order = Order.objects.get(supplier__name='Connector Supply Co.')
        line = order.lines.first()
        line.quantity = 25
        line.save()
        e = self.entries(module='orders').last()
        self.assertEqual((e.object_label, e.item_type), (str(order), 'order line'))
        self.assertEqual(e.object_url, order.get_absolute_url())

    def test_secrets_and_logins_are_not_logged(self):
        bob = User.objects.get(username='bob')
        bob.set_password('new-secret')
        bob.save()
        e = self.entries(module='users', object_id=bob.pk).last()
        self.assertNotIn('new-secret', str(e.changes))
        self.assertNotIn(bob.password, str(e.changes))
        self.assertEqual(e.changes, [{'field': 'password', 'old': '', 'new': '(changed)'}])
        self.client.login(username='bob', password='new-secret')  # last_login changes
        self.assertEqual(self.entries(module='users', object_id=bob.pk).count(), 1)

    def test_nothing_breaks_without_a_request(self):
        Part.objects.create(part_number='LOG-1')
        e = self.entries(module='inventory').last()
        self.assertEqual((e.actor_module, e.chain_id, e.user), ('', '', None))


class ChainTests(AuditTestCase):
    def test_order_to_tasks_to_stock_is_one_traceable_chain(self):
        order = Order.objects.get(supplier__name='Connector Supply Co.')
        # 1. Placing the order (Orders page) makes a rule create tasks.
        self.client.post(reverse('orders:set_status', args=[order.pk]), {'status': 'placed'})
        placed = self.entries(module='orders', object_id=order.pk).get()
        self.assertEqual(placed.triggered_by, '')
        tasks = self.entries(module='tasks', action='created')
        self.assertEqual(tasks.count(), 2)
        for e in tasks:
            self.assertEqual(e.triggered_by, 'automations')
            self.assertEqual(e.chain_id, placed.chain_id)
            self.assertEqual(e.chain_path, 'orders > automations')
            self.assertEqual(e.source, 'rule "Check in placed orders"')

        # 2. Finishing the tasks (Tasks pages) makes a rule receive the order, which books stock.
        for t in Task.objects.filter(batch__isnull=False):
            self.client.post(reverse('tasks:set_status', args=[t.pk]), {'status': 'done'})
        received = self.entries(module='orders', object_id=order.pk, action='changed').last()
        self.assertEqual(received.triggered_by, 'automations')
        self.assertEqual(received.chain_path, 'tasks > automations')
        stock = self.entries(module='inventory', item_type='stock move')
        self.assertEqual(stock.count(), 2)
        for e in stock:
            self.assertEqual(e.triggered_by, 'orders')
            self.assertEqual(e.chain_path, 'tasks > automations > orders')
            self.assertEqual(e.chain_id, received.chain_id)
        self.assertNotEqual(received.chain_id, placed.chain_id)

        # The chain page shows every module's part of it, in order.
        page = self.client.get(reverse('audit:chain', args=[received.chain_id]))
        self.assertContains(page, 'Received with')
        self.assertContains(page, str(order))
        chains = self.client.get(reverse('audit:chains'))
        self.assertContains(chains, received.chain_id)
        self.assertContains(chains, placed.chain_id)

    def test_acting_nests_and_keeps_the_chain(self):
        with audit.acting('orders', user=self.admin) as outer:
            with audit.acting('automations', source='rule "x"'):
                Task.objects.create(title='Nested')
                self.assertEqual(audit.chain_path(), ['orders', 'automations'])
        e = self.entries(module='tasks').last()
        self.assertEqual((e.chain_id, e.user, e.source, e.triggered_by), (outer['chain_id'], self.admin, 'rule "x"', 'automations'))
        self.assertIsNone(audit.current())


class PageTests(AuditTestCase):
    def test_pages_and_filters(self):
        task = Task.objects.get(title__startswith='Repair returned PDU')
        self.client.post(reverse('tasks:set_status', args=[task.pk]), {'status': 'done'})
        ct = ContentType.objects.get_for_model(Task)
        for url in (reverse('audit:list'), reverse('audit:list') + '?module=tasks&action=changed&chained=1&q=repair&user=none',
                    reverse('audit:chains'), reverse('audit:activity'), reverse('audit:history', args=[ct.pk, task.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.get(reverse('audit:list') + '?module=tasks'), 'Repair returned PDU')
        self.assertNotContains(self.client.get(reverse('audit:list') + '?module=inventory'), 'Repair returned PDU')
        # Each module's menu links to its log, and detail pages show a History panel.
        page = self.client.get(task.get_absolute_url())
        self.assertContains(page, reverse('audit:list') + '?module=tasks')
        self.assertContains(page, 'Full history')
        self.assertEqual(self.client.get(reverse('audit:chain', args=['nope'])).status_code, 404)


class HistorySettingTests(AuditTestCase):
    def test_hiding_a_modules_history_hides_the_pages_but_keeps_logging(self):
        task = Task.objects.get(title__startswith='Repair returned PDU')
        self.client.post(reverse('audit:settings'), {'shown': ['inventory', 'orders']})  # tasks unticked
        self.assertFalse(HiddenHistory.shown('tasks'))
        self.assertTrue(HiddenHistory.shown('orders'))
        self.client.post(reverse('tasks:set_status', args=[task.pk]), {'status': 'done'})
        self.assertTrue(self.entries(module='tasks', object_id=task.pk).exists())  # still logged
        page = self.client.get(task.get_absolute_url())
        self.assertNotContains(page, reverse('audit:list') + '?module=tasks')
        self.assertNotContains(page, 'Full history')
        self.assertContains(self.client.get(reverse('audit:list') + '?module=tasks'), 'Repair returned PDU')
        # Ticking it again brings it back.
        self.client.post(reverse('audit:settings'), {'shown': ['tasks']})
        self.assertContains(self.client.get(task.get_absolute_url()), 'Full history')

    def test_only_an_administrator_can_change_it(self):
        self.client.login(username='alice', password='demo')
        self.assertEqual(self.client.get(reverse('audit:settings')).status_code, 200)
        self.assertEqual(self.client.post(reverse('audit:settings'), {}).status_code, 403)
        self.assertFalse(HiddenHistory.objects.exists())
