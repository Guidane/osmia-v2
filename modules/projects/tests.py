from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from budgets.models import Budget, Spending, TaskBudget
from departments.models import Department
from inventory.models import Part
from orders.models import Order, OrderLine
from stock.models import StockMove
from tasks.models import Task
from users.models import User

from .models import Project, ProjectTask


class ProjectTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())
        from automations.models import Rule
        Rule.objects.update(active=False)  # placing an order would otherwise make check-in tasks

    def setUp(self):
        self.client.login(username='admin', password='admin')
        self.pdu = Project.objects.get(name='PDU-100 production run')
        self.eng = Budget.objects.get(budget_number='B-ENG')

    def test_demo_projects_have_budgets_and_tasks(self):
        self.assertEqual(Project.objects.count(), 3)
        b = self.pdu.budget
        self.assertEqual((b.name, b.budget_number, b.amount, b.parent, b.department),
                         ('PDU-100 production run', self.pdu.code, Decimal(40_000), self.eng, self.pdu.department))
        self.assertTrue(self.pdu.code.startswith('PRJ-'))
        tasks = Task.objects.filter(project_link__project=self.pdu)
        self.assertTrue(tasks.filter(title__startswith='Build PDU-100').exists())
        self.assertTrue(tasks.filter(title='Kit the parts from stock').exists())  # subtasks too
        self.assertFalse(TaskBudget.objects.filter(task__in=tasks).exclude(budget=b).exists())

    def test_create_project_with_its_budget(self):
        qa = Department.objects.get(name='QA')
        resp = self.client.post(reverse('projects:create'), {
            'name': 'Rack upgrade', 'status': 'active', 'department': qa.pk, 'budget_amount': '2500.00',
            'budget_parent': self.eng.pk, 'start_date': '2026-11-01', 'end_date': '2026-12-01',
            'members': [User.objects.get(username='bob').pk],
        })
        project = Project.objects.get(name='Rack upgrade')
        self.assertRedirects(resp, project.get_absolute_url())
        self.assertEqual((project.budget.amount, project.budget.parent, project.budget.department), (Decimal('2500.00'), self.eng, qa))
        self.assertEqual(project.created_by.username, 'admin')

        # Editing it keeps its budget in step: name, amount, department, where it sits.
        self.client.post(reverse('projects:edit', args=[project.pk]), {
            'name': 'Rack upgrade 2', 'code': project.code, 'status': 'active', 'budget_amount': '3000', 'budget_parent': '',
        })
        project.refresh_from_db()
        self.assertEqual(Budget.objects.filter(project=project).count(), 1)
        self.assertEqual((project.budget.name, project.budget.amount, project.budget.parent, project.budget.department),
                         ('Rack upgrade 2', Decimal(3000), None, None))

        resp = self.client.post(reverse('projects:edit', args=[project.pk]), {
            'name': 'x', 'code': project.code, 'status': 'active', 'budget_amount': '1',
            'start_date': '2026-12-01', 'end_date': '2026-11-01'})
        self.assertContains(resp, 'before the start date')

    def test_tasks_are_charged_to_the_project_budget(self):
        project = Project.objects.get(name='Automatic test bench')
        task = Task.objects.get(title='Sort the resistor drawers by value')
        self.client.post(reverse('projects:add_task', args=[project.pk]), {'task': task.pk})
        self.assertEqual(task.project_link.project, project)
        self.assertEqual(TaskBudget.objects.get(task=task).budget, project.budget)

        self.client.post(reverse('projects:add_task', args=[project.pk]), {'title': 'Order the bench frame', 'due_date': '2026-11-20'})
        new = Task.objects.get(title='Order the bench frame')
        self.assertEqual((new.department, new.budget_link.budget), (project.department, project.budget))

        self.client.post(reverse('projects:remove_task', args=[project.pk, task.pk]))
        self.assertFalse(ProjectTask.objects.filter(task=task).exists())
        self.assertFalse(TaskBudget.objects.filter(task=task).exists())
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())  # the task is kept

        # From the task's page: move it from one project to another.
        self.client.post(reverse('projects:set_task_project', args=[new.pk]), {'project': self.pdu.pk})
        self.assertEqual(ProjectTask.objects.get(task=new).project, self.pdu)
        self.assertEqual(TaskBudget.objects.get(task=new).budget, self.pdu.budget)
        self.assertContains(self.client.get(new.get_absolute_url()), self.pdu.get_absolute_url())

    def test_spending_from_materials_and_orders(self):
        task = Task.objects.filter(title='Kit the parts from stock', project_link__project=self.pdu).first()
        part = Part.objects.get(part_number='PSU-24V-150W')
        before = Spending()
        before.annotate([self.pdu.budget])
        move = StockMove.record(part, StockMove.Type.OUT, -1, location=part.stock_items.filter(quantity__gt=0).first().location,
                                task=task)
        order = Order.objects.create(budget=self.pdu.budget)
        OrderLine.objects.create(order=order, part=part, quantity=2, unit_price=Decimal('50'))
        order.status = Order.Status.PLACED
        order.save()

        page = self.client.get(self.pdu.get_absolute_url())
        b = page.context['project'].budget
        self.assertEqual(b.spent, before.total[self.pdu.budget_id] + move.unit_cost + Decimal(100))
        self.assertEqual(page.context['by_source']['Orders'], Decimal(100))
        self.assertIn(move, page.context['materials'])
        self.assertIn(order, page.context['orders'])
        self.assertContains(page, order.number)
        # The order's and budget's pages point back to the project.
        self.assertContains(self.client.get(order.get_absolute_url()), self.pdu.get_absolute_url())
        self.assertContains(self.client.get(self.pdu.budget.get_absolute_url()), 'This is the budget of')
        sub = Budget.objects.create(name='Tooling', parent=self.pdu.budget, amount=1000)
        self.assertContains(self.client.get(sub.get_absolute_url()), 'This budget is part of')

    def test_pages_and_permissions(self):
        for url in (reverse('projects:list'), reverse('projects:list') + '?status=all&q=PDU', reverse('projects:create'),
                    self.pdu.get_absolute_url(), reverse('projects:edit', args=[self.pdu.pk]),
                    self.pdu.department.get_absolute_url(), User.objects.get(username='carla').get_absolute_url(),
                    reverse('core:home')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.get(reverse('projects:list')), 'PDU-100 production run')
        self.assertNotContains(self.client.get(reverse('projects:list') + '?status=done'), 'PDU-100 production run')

        # Bob is on the team but neither the manager nor allowed to change projects.
        self.client.login(username='bob', password='demo')
        self.assertEqual(self.client.get(reverse('projects:edit', args=[self.pdu.pk])).status_code, 403)
        task = Task.objects.get(title='Sort the resistor drawers by value')
        self.assertEqual(self.client.post(reverse('projects:add_task', args=[self.pdu.pk]), {'task': task.pk}).status_code, 403)
        self.assertNotContains(self.client.get(self.pdu.get_absolute_url()), 'Add task')
        # Alice manages it, so she can.
        self.client.login(username='alice', password='demo')
        self.assertEqual(self.client.get(reverse('projects:edit', args=[self.pdu.pk])).status_code, 200)
        self.client.post(reverse('projects:add_task', args=[self.pdu.pk]), {'task': task.pk})
        self.assertEqual(task.project_link.project, self.pdu)
