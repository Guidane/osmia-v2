from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from inventory.models import Part

from .models import Tool


class ToolTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        self.client.login(username='admin', password='admin')

    def test_create_list_and_parts(self):
        resp = self.client.post(reverse('tools:create'), {
            'name': 'Torque driver', 'part_number': 'TD-5', 'kind': 'hand', 'manufacturer': 'Wera',
            'storage': 'Bench 1', 'notes': '', 'is_active': 'on',
        })
        tool = Tool.objects.get(name='Torque driver')
        self.assertRedirects(resp, tool.get_absolute_url())
        crimp = Tool.objects.get(name='D-sub crimp tool')
        contact = Part.objects.get(part_number='DS-PIN-C')
        self.assertIn(crimp, contact.tools.all())  # demo: contacts are crimped with it
        page = self.client.get(crimp.get_absolute_url())
        self.assertContains(page, 'DS-PIN-C')
        self.assertContains(page, '58448-2')
        self.assertContains(page, 'Images')  # the gallery
        listing = self.client.get(reverse('tools:list') + '?q=crimp')
        self.assertContains(listing, 'D-sub crimp tool')
        self.assertNotContains(listing, 'Torque driver')
        tool.is_active = False
        tool.save()
        self.assertNotContains(self.client.get(reverse('tools:list')), 'Torque driver')
        self.assertContains(self.client.get(reverse('tools:list') + '?retired=1'), 'Torque driver')
        self.assertEqual(self.client.get(reverse('tools:edit', args=[tool.pk])).status_code, 200)
