from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.modules import dependency_order, get_module
from users.models import User

from .models import Department, department_of


class DepartmentModuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        self.client.login(username='admin', password='admin')

    def test_is_its_own_module_after_users(self):
        labels = [c.label for c in dependency_order()]
        self.assertLess(labels.index('users'), labels.index('departments'))
        self.assertNotIn('departments', get_module('users').manifest.depends)
        self.assertEqual([m.url_name for m in get_module('departments').manifest.menu], ['departments:list', 'departments:create'])
        self.assertNotIn('departments:list', [m.url_name for m in get_module('users').manifest.menu])

    def test_pages(self):
        warehouse = Department.objects.get(name='Warehouse')
        for url in [reverse('departments:list'), reverse('departments:create'), warehouse.get_absolute_url(),
                    reverse('departments:edit', args=[warehouse.pk])]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(warehouse.get_absolute_url().startswith('/departments/'))
        self.assertContains(self.client.get(warehouse.get_absolute_url()), 'Bob Nguyen')

    def test_create_needs_permission(self):
        self.client.login(username='bob', password='demo')
        self.assertEqual(self.client.get(reverse('departments:create')).status_code, 403)
        self.client.login(username='admin', password='admin')
        ops = Department.objects.get(name='Operations', parent=None)
        self.client.post(reverse('departments:create'), {'name': 'Quality', 'parent': ops.pk})
        self.assertEqual(Department.objects.get(name='Quality').full_path(), 'Operations > Quality')

    def test_user_pages_show_the_department(self):
        bob = User.objects.get(username='bob')
        self.assertEqual(department_of(bob).full_path(), 'Operations > Warehouse')
        self.assertIn(bob, Department.objects.get(name='Operations', parent=None).members(include_sub=True))
        self.assertContains(self.client.get(bob.get_absolute_url()), f'href="{department_of(bob).get_absolute_url()}"')
        resp = self.client.get(reverse('users:list'))
        self.assertContains(resp, '<th>Department</th>', html=True)
        self.assertContains(resp, 'All departments')

    def test_manager_sets_the_department_on_the_user_form(self):
        marketing = Department.objects.get(name='Marketing')
        resp = self.client.post(reverse('users:create'), {
            'username': 'dana', 'password1': 'a-Long-pass-123', 'password2': 'a-Long-pass-123',
            'first_name': 'Dana', 'last_name': '', 'email': '', 'job_title': '', 'phone': '',
            'dept-department': marketing.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(department_of(User.objects.get(username='dana')), marketing)
        bob = User.objects.get(username='bob')
        self.client.post(reverse('users:edit', args=[bob.pk]), {
            'username': 'bob', 'first_name': 'Bob', 'last_name': 'Nguyen', 'email': '', 'job_title': '', 'phone': '',
            'is_active': 'on', 'dept-department': '',
        })
        self.assertIsNone(department_of(bob))
