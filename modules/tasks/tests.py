from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import Task, TaskGroup


class SubtaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        self.client.login(username='admin', password='admin')
        self.count = Task.objects.get(title='Quarterly stock count')
        self.aisle1 = Task.objects.get(title='Count aisle 1')

    def test_the_list_is_a_tree_table(self):
        page = self.client.get(reverse('tasks:list') + '?status=all')
        self.assertContains(page, 'data-tree="tasks"')
        self.assertContains(page, f'data-id="{self.aisle1.pk}" data-parent="{self.count.pk}" data-depth="1"')
        self.assertContains(page, '1/3')  # subtasks done
        shelf = Task.objects.get(title__startswith='Shelf 2B')
        self.assertContains(page, f'data-id="{shelf.pk}" data-parent="{shelf.parent_id}" data-depth="2"')
        rows = page.context['rows']
        self.assertEqual(rows[rows.index(self.count) + 1].parent_id, self.count.pk)  # subtasks follow their parent
        # Aisle 1 is done, so the open list leaves it out but keeps its open siblings under the parent.
        rows = self.client.get(reverse('tasks:list')).context['rows']
        self.assertNotIn(self.aisle1, rows)
        self.assertIn(Task.objects.get(title='Count aisle 2'), rows)

    def test_a_subtask_whose_parent_is_filtered_out_shows_the_parent(self):
        page = self.client.get(reverse('tasks:list') + '?q=aisle&status=all')
        self.assertContains(page, 'Quarterly stock count › ')
        self.assertContains(page, f'data-id="{self.aisle1.pk}" data-parent="" data-depth="0"')

    def test_adding_a_subtask_and_the_detail_page(self):
        form = self.client.get(reverse('tasks:create') + f'?parent={self.aisle1.pk}')
        self.assertEqual(str(form.context['form'].initial['parent']), str(self.aisle1.pk))
        self.client.post(reverse('tasks:create'), {'title': 'Shelf A', 'parent': self.aisle1.pk, 'status': 'todo', 'priority': 1})
        shelf = Task.objects.get(title='Shelf A')
        self.assertEqual(shelf.ancestors(), [self.count, self.aisle1])
        page = self.client.get(self.count.get_absolute_url())
        self.assertContains(page, 'Shelf A')  # the whole subtree
        self.assertContains(self.client.get(shelf.get_absolute_url()), 'Part of')

    def test_demo_data_has_trees_and_loading_it_again_adds_nothing(self):
        self.assertEqual(Task.objects.get(title='Count aisle 2').subtasks.count(), 3)
        self.assertEqual(len(Task.objects.get(title='Test the wrist straps').ancestors()), 2)
        self.assertTrue(Task.objects.filter(title__startswith='Repair returned PDU', parent=None).get().subtasks.exists())
        before = Task.objects.count()
        call_command('load_demo', stdout=StringIO())
        self.assertEqual(Task.objects.count(), before)

    def test_a_task_cant_go_under_itself_or_its_subtasks(self):
        edit = self.client.get(reverse('tasks:edit', args=[self.count.pk]))
        parents = edit.context['form'].fields['parent'].queryset
        self.assertNotIn(self.count, parents)
        self.assertNotIn(self.aisle1, parents)

    def test_deleting_a_parent_keeps_its_subtasks(self):
        self.client.post(reverse('tasks:delete', args=[self.count.pk]))
        self.aisle1.refresh_from_db()
        self.assertIsNone(self.aisle1.parent)


class TaskGroupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        self.client.login(username='admin', password='admin')
        self.inventory = TaskGroup.objects.get(name='Inventory')
        self.quality = TaskGroup.objects.get(name='Quality')
        self.count = Task.objects.get(title='Quarterly stock count')

    def test_demo_data_has_about_a_hundred_tasks_in_groups(self):
        self.assertGreaterEqual(Task.objects.count(), 100)
        self.assertEqual(self.count.group, self.inventory)
        self.assertEqual(Task.objects.get(title__startswith='Shelf 2B').group, self.inventory)  # subtasks too
        self.assertTrue(Task.objects.filter(group=None, assignee__isnull=False).exists())

    def test_moving_tasks_to_a_group_moves_their_subtasks(self):
        self.client.post(reverse('tasks:move'), {'tasks': [self.count.pk], 'group': self.quality.pk})
        self.assertEqual(set(Task.objects.filter(pk__in={self.count.pk, *self.count.descendant_ids()})
                             .values_list('group__name', flat=True)), {'Quality'})
        self.client.post(reverse('tasks:move'), {'tasks': [self.count.pk], 'group': ''})
        self.assertIsNone(Task.objects.get(title__startswith='Shelf 2B').group)

    def test_changing_the_group_on_the_form_moves_the_subtasks(self):
        edit = self.client.get(reverse('tasks:edit', args=[self.count.pk])).context['form']
        data = {k: v for k, v in edit.initial.items() if v is not None and k in edit.fields}
        data.update(group=self.quality.pk, parent='', assignee=self.count.assignee_id, department=self.count.department_id or '')
        self.client.post(reverse('tasks:edit', args=[self.count.pk]), data)
        self.assertEqual(Task.objects.get(title__startswith='Shelf 2B').group, self.quality)

    def test_a_new_subtask_starts_in_its_parents_group(self):
        form = self.client.get(reverse('tasks:create') + f'?parent={self.count.pk}').context['form']
        self.assertEqual(form.initial['group'], self.inventory.pk)

    def test_list_filter_gantt_and_group_pages(self):
        listed = self.client.get(reverse('tasks:list') + f'?group={self.inventory.pk}&status=all').context['rows']
        self.assertTrue(listed and all(t.group == self.inventory for t in listed))
        page = self.client.get(reverse('tasks:list'))
        self.assertContains(page, 'Move to group')
        self.assertContains(page, 'class="group-tag"')
        gantt = self.client.get(reverse('tasks:gantt') + '?weeks=12')
        names = [s['group'].name if s['group'] else None for s in gantt.context['sections']]
        self.assertEqual(names[:2], ['Inventory', 'Maintenance'])  # by name, then no group
        self.assertEqual(names[-1], None)
        self.assertContains(gantt, 'gantt-group-bar')
        self.assertContains(self.client.get(reverse('tasks:groups')), 'Inventory')
        self.client.post(reverse('tasks:group_create'), {'name': 'Office', 'color': TaskGroup.Color.TEAL})
        self.assertTrue(TaskGroup.objects.filter(name='Office').exists())
        self.client.post(reverse('tasks:group_delete', args=[self.inventory.pk]))
        self.count.refresh_from_db()
        self.assertIsNone(self.count.group)  # the tasks stay
