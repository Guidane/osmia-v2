import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from assemblies.models import Assembly
from core import automation
from inventory.models import Part
from stock.models import PartStock, StockItem, on_hand
from orders.models import Order
from tasks.models import Task, TaskBatch
from users.models import User

from . import engine
from .models import Notification, Rule, Run


class AutomationTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        self.client.login(username='admin', password='admin')
        self.order = Order.objects.get(supplier__name='Connector Supply Co.')
        self.check_in = Rule.objects.get(trigger='orders.order_placed')
        self.receive = Rule.objects.get(trigger='tasks.batch_completed')
        self.alice = User.objects.get(username='alice')

    def finish(self, tasks):
        for t in tasks:
            t = Task.objects.get(pk=t.pk)  # as a page would load it
            t.status = Task.Status.DONE
            t.save()


class ChainTests(AutomationTestCase):
    def test_placing_an_order_creates_tasks_and_finishing_them_receives_it(self):
        part = Part.objects.get(part_number='DB25-F')
        before = on_hand(part)
        self.order.set_status(Order.Status.PLACED)

        tasks = list(Task.objects.filter(batch__rule_id=self.check_in.pk))
        self.assertEqual(len(tasks), 2)
        self.assertIn(self.order.number, tasks[0].title)
        self.assertEqual({t.assignee.username for t in tasks}, {'bob', 'carla'})
        self.assertTrue(all(t.due_date for t in tasks))
        self.assertEqual(TaskBatch.objects.get().source, self.order)
        self.assertEqual(Notification.objects.filter(user=self.alice).count(), 1)
        self.assertEqual(Run.objects.get(rule=self.check_in).status, Run.Status.OK)

        self.finish(tasks[:1])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PLACED)  # one task still open

        self.finish(tasks[1:])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.RECEIVED)
        part.refresh_from_db()
        self.assertEqual(on_hand(part), before + 20)
        run = Run.objects.get(rule=self.receive)
        self.assertEqual(run.status, Run.Status.OK)
        self.assertEqual(run.source_label, str(self.order))
        note = Notification.objects.filter(user=self.alice).first()
        self.assertIn('received into stock', note.message)
        self.assertEqual(note.url, self.order.get_absolute_url())

        # Reopening and finishing a task again doesn't complete the group twice.
        t = Task.objects.get(pk=tasks[0].pk)
        t.status = Task.Status.TODO
        t.save()
        self.finish(tasks[:1])
        self.assertEqual(Run.objects.filter(rule=self.receive).count(), 1)
        self.assertEqual(part.moves.filter(note__contains=self.order.number).count(), 1)

    def test_inactive_rules_do_nothing(self):
        Rule.objects.update(active=False)
        self.order.set_status(Order.Status.PLACED)
        self.assertFalse(Task.objects.filter(batch__isnull=False).exists())
        self.assertFalse(Run.objects.exists())

    def test_a_failing_step_undoes_the_rule_but_not_the_trigger(self):
        self.check_in.actions.append({'action': 'core.set_status', 'params': {'target': 'object', 'status': 'tasks.task:done'}})
        self.check_in.save()
        self.order.set_status(Order.Status.PLACED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PLACED)
        self.assertFalse(Task.objects.filter(batch__isnull=False).exists())
        self.assertFalse(Notification.objects.filter(rule__isnull=False).exists())  # none from the rules
        run = Run.objects.get(rule=self.check_in)
        self.assertEqual(run.status, Run.Status.FAILED)
        self.assertIn('undone', run.log)

    def test_self_triggering_rules_stop(self):
        Rule.objects.create(name='Loop', trigger='tasks.task_created', actions=[
            {'action': 'tasks.create_tasks', 'params': {'tasks': [{'title': 'Again'}]}},
        ])
        Task.objects.create(title='Start')
        self.assertLessEqual(Task.objects.filter(title='Again').count(), automation.MAX_DEPTH)


class ConditionTests(AutomationTestCase):
    def test_operators(self):
        fields = {f.key: f for f in automation.events()['orders.order_placed'].fields}
        cases = [
            ({'field': 'supplier', 'op': 'is', 'value': 'connector supply co.'}, True),
            ({'field': 'supplier', 'op': 'is_not', 'value': 'Connector Supply Co.'}, False),
            ({'field': 'supplier', 'op': 'contains', 'value': 'supply'}, True),
            ({'field': 'total', 'op': 'gt', 'value': '50'}, True),
            ({'field': 'total', 'op': 'lt', 'value': '50'}, False),
            ({'field': 'total', 'op': 'gt', 'value': 'lots'}, False),
            ({'field': 'nope', 'op': 'is', 'value': ''}, False),
        ]
        for condition, expected in cases:
            self.assertEqual(engine.test(condition, fields, self.order), expected, condition)

    def test_condition_that_fails_skips_the_rule(self):
        self.check_in.conditions = [{'field': 'supplier', 'op': 'is', 'value': 'Someone else'}]
        self.check_in.save()
        self.order.set_status(Order.Status.PLACED)
        self.assertFalse(Run.objects.exists())


class ActionTests(AutomationTestCase):
    def test_stock_move_and_low_stock_trigger(self):
        part = Part.objects.get(part_number='DB25-M')
        PartStock.objects.filter(part=part).update(reorder_level=on_hand(part) - 1)
        Rule.objects.create(name='Low', trigger='stock.stock_low', actions=[
            {'action': 'automations.notify', 'params': {'users': [self.alice.pk], 'message': '{object} is low'}},
        ])
        rule = Rule.objects.create(name='Issue', trigger='assemblies.completed', actions=[
            {'action': 'stock.stock_move', 'params': {'part': part.pk, 'direction': 'out', 'quantity': '2', 'note': 'For {object}'}},
        ])
        before = on_hand(part)
        assembly = Assembly.objects.filter(status=Assembly.Status.MANUFACTURING).first()
        assembly.status = Assembly.Status.COMPLETED
        assembly.save()
        part.refresh_from_db()
        self.assertEqual(on_hand(part), before - 2)
        self.assertEqual(Run.objects.get(rule=rule).status, Run.Status.OK)
        self.assertTrue(Notification.objects.filter(message__endswith='is low').exists())

    def test_notify_owner(self):
        task = Task.objects.create(title='Mine', assignee=self.alice)
        rule = Rule.objects.create(name='Tell', trigger='tasks.task_completed', actions=[
            {'action': 'automations.notify', 'params': {'owner': True, 'message': '{object} done'}},
        ])
        self.finish([task])
        self.assertEqual(Notification.objects.get(rule=rule).user, self.alice)


class PageTests(AutomationTestCase):
    def post_rule(self, url, **overrides):
        data = {
            'name': 'Welcome', 'description': '', 'active': 'on', 'trigger': 'tasks.task_created',
            'conditions': json.dumps([{'field': 'title', 'op': 'contains', 'value': 'new hire'}, {'field': '', 'op': 'is'}]),
            'actions': json.dumps([{'action': 'automations.notify', 'params': {
                'users': [self.alice.pk, 99999], 'message': 'Hello {object}', 'link': 'object', 'bogus': 1}}]),
        }
        data.update(overrides)
        return self.client.post(url, data)

    def test_builder_saves_a_clean_rule(self):
        self.post_rule(reverse('automations:create'))
        rule = Rule.objects.get(name='Welcome')
        self.assertEqual(rule.conditions, [{'field': 'title', 'op': 'contains', 'value': 'new hire'}])
        self.assertEqual(rule.actions[0]['params'], {'users': [self.alice.pk], 'owner': False, 'message': 'Hello {object}', 'link': 'object'})
        self.assertEqual(rule.created_by.username, 'admin')

    def test_builder_rejects_bad_rules(self):
        for overrides in (
            {'actions': '[]'},
            {'actions': 'not json'},
            {'actions': json.dumps([{'action': 'automations.notify', 'params': {'message': ''}}])},
            {'conditions': json.dumps([{'field': 'priority', 'op': 'gt', 'value': 'x'}])},
            {'trigger': 'nope.nothing'},
        ):
            response = self.post_rule(reverse('automations:create'), **overrides)
            self.assertEqual(response.status_code, 200, overrides)
        self.assertFalse(Rule.objects.filter(name='Welcome').exists())

    def test_pages(self):
        self.order.set_status(Order.Status.PLACED)
        note = Notification.objects.get(user=self.alice)
        for url in (reverse('automations:list'), reverse('automations:create'), self.check_in.get_absolute_url(),
                    reverse('automations:edit', args=[self.check_in.pk]), reverse('automations:runs'),
                    reverse('automations:notifications'), self.order.get_absolute_url()):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertContains(self.client.get(self.order.get_absolute_url()), 'Tasks from automations')
        self.assertContains(self.client.get(self.check_in.get_absolute_url()), 'Take in the delivery of {source}')

        self.client.login(username='alice', password='demo')
        self.assertContains(self.client.get(reverse('automations:list')), 'class="count">1<')
        response = self.client.get(reverse('automations:open_notification', args=[note.pk]))
        self.assertRedirects(response, self.order.get_absolute_url(), fetch_redirect_response=False)
        note.refresh_from_db()
        self.assertTrue(note.read)

    def test_edit_page_keeps_the_trigger(self):
        response = self.client.get(reverse('automations:edit', args=[self.receive.pk]))
        self.assertContains(response, '<option value="tasks.batch_completed" selected>')

    def test_toggle(self):
        self.client.post(reverse('automations:toggle', args=[self.check_in.pk]))
        self.check_in.refresh_from_db()
        self.assertFalse(self.check_in.active)
