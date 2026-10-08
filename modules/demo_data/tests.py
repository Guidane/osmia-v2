from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from inventory.models import Category, Part, PartAttributeValue
from tasks.models import Task
from users.models import User

from .models import DemoRecord, LogMark, demo_objects


class DemoDataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        self.client.login(username='admin', password='admin')
        self.admin = User.objects.get(username='admin')

    def demo(self):
        return {obj for _, obj in demo_objects(keep_user=self.admin)}

    def test_loading_demo_data_records_what_it_made(self):
        demo = self.demo()
        self.assertIn(Task.objects.get(title='Quarterly stock count'), demo)
        self.assertIn(User.objects.get(username='alice'), demo)
        self.assertNotIn(self.admin, demo)  # administrators never count as demo data
        page = self.client.get(reverse('demo_data:index'))
        self.assertContains(page, 'Turn demo data off')
        self.assertContains(page, 'Tasks')

    def test_turning_it_off_removes_demo_data_and_keeps_your_own(self):
        own_task = Task.objects.create(title='My own task', created_by=self.admin)
        electrical = Category.objects.get(name='Electrical', parent=None)
        own_category = Category.objects.create(name='My cables', parent=electrical)  # a demo category in use
        part = Part.objects.get(part_number='DB25-M')
        attribute = part.category.attributes.first()
        part.attribute_values.filter(attribute=attribute).delete()
        own_value = PartAttributeValue.objects.create(part=part, attribute=attribute, value='mine')  # goes with the part

        preview = self.client.get(reverse('demo_data:remove'))
        self.assertContains(preview, 'of your own record')
        self.assertContains(preview, 'will stay')
        self.assertTrue(Task.objects.filter(title='Quarterly stock count').exists())  # the preview changed nothing
        self.assertTrue(PartAttributeValue.objects.filter(pk=own_value.pk).exists())

        self.client.post(reverse('demo_data:remove'))
        self.assertFalse(Task.objects.filter(title='Quarterly stock count').exists())
        self.assertFalse(User.objects.filter(username='alice').exists())
        self.assertFalse(Part.objects.filter(part_number='DB25-M').exists())
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())
        self.assertTrue(Task.objects.filter(pk=own_task.pk).exists())
        self.assertTrue(Category.objects.filter(pk=own_category.pk).exists())
        self.assertEqual(self.demo(), {electrical})  # kept: your category is in it
        # And on again.
        self.client.post(reverse('demo_data:load'))
        self.assertTrue(Task.objects.filter(title='Quarterly stock count').exists())
        self.assertGreaterEqual(Task.objects.count(), 100)

    def test_demo_data_from_before_the_module_is_found_in_the_change_log(self):
        DemoRecord.objects.all().delete()  # as if loaded before this module was installed
        LogMark.objects.all().delete()
        demo = self.demo()
        for obj in (Task.objects.get(title='Count aisle 2'), User.objects.get(username='bob'),
                    Part.objects.get(part_number='DB25-F')):
            self.assertIn(obj, demo)
        # Child rows (a part's attribute values) aren't in the log on their own, but go with their parent.
        self.client.post(reverse('demo_data:remove'))
        self.assertFalse(Part.objects.filter(part_number='DB25-F').exists())
        self.assertFalse(PartAttributeValue.objects.filter(part__part_number='DB25-F').exists())
        self.assertFalse(Task.objects.filter(title='Count aisle 2').exists())

    def test_only_an_administrator_can_turn_it_on_or_off(self):
        self.client.login(username='alice', password='demo')
        self.assertEqual(self.client.get(reverse('demo_data:index')).status_code, 200)
        self.assertEqual(self.client.post(reverse('demo_data:load')).status_code, 403)
        self.assertEqual(self.client.post(reverse('demo_data:remove')).status_code, 403)
        self.assertTrue(User.objects.filter(username='bob').exists())
