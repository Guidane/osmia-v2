from pathlib import Path
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.apps import check_module_dependencies
from core.modules import dependency_order
from inventory.models import Part
from stock.models import StockItem, StockMove, on_hand
from assemblies.models import Assembly
from tasks.models import Task
from users.models import User


class ModuleFrameworkTests(TestCase):
    def test_dependency_order(self):
        labels = [c.label for c in dependency_order()]
        self.assertLess(labels.index('users'), labels.index('tasks'))
        self.assertLess(labels.index('tasks'), labels.index('inventory'))
        self.assertLess(labels.index('inventory'), labels.index('assemblies'))

    def test_no_dependency_errors(self):
        self.assertEqual(check_module_dependencies(None), [])


class PageSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        self.client.login(username='admin', password='admin')

    def test_login_required(self):
        self.client.logout()
        resp = self.client.get(reverse('tasks:board'))
        self.assertRedirects(resp, reverse('users:login') + '?next=' + reverse('tasks:board'))

    def test_all_pages_render(self):
        user = User.objects.get(username='carla')
        task = Task.objects.get(title__startswith='Repair returned PDU')
        part = Part.objects.get(part_number='PSU-24V-150W')
        location = StockItem.objects.filter(part=part).first().location
        urls = [
            reverse('core:home'),
            reverse('users:list'), reverse('users:create'),
            reverse('users:detail', args=[user.pk]), reverse('users:edit', args=[user.pk]),
            reverse('tasks:board'), reverse('tasks:list'), reverse('tasks:gantt'),
            reverse('tasks:gantt') + '?weeks=12&start=2020-01-01&status=open&assignee=none',
            reverse('tasks:gantt') + '?weeks=abc&start=garbage', reverse('tasks:mine'), reverse('tasks:create'),
            reverse('tasks:detail', args=[task.pk]), reverse('tasks:edit', args=[task.pk]),
            reverse('tasks:delete', args=[task.pk]),
            reverse('inventory:part_list') + '?q=stainless m3', reverse('inventory:part_create'),
            reverse('inventory:part_detail', args=[part.pk]),
            reverse('inventory:part_edit', args=[part.pk]),
            reverse('stock:list'), reverse('stock:move_list'), reverse('stock:move_create') + f'?task={task.pk}',
            reverse('inventory:category_list'), reverse('inventory:category_create'),
            reverse('inventory:category_edit', args=[part.category_id]),
            reverse('stock:location_list'), reverse('stock:location_create'),
            reverse('stock:location_edit', args=[location.pk]),
            reverse('stock:location_detail', args=[location.pk]),
            reverse('tools:list'), reverse('tools:create'),
            reverse('assemblies:list'), reverse('assemblies:create'),
        ] + [reverse(name, args=[a.pk]) for a in Assembly.objects.all() for name in ('assemblies:detail', 'assemblies:edit')]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_home_shows_only_modules(self):
        resp = self.client.get(reverse('core:home'))
        for title in ('Users', 'Tasks', 'Parts', 'Stock', 'Tools', 'Assemblies', 'Budgets', 'Devices', 'Harness'):
            self.assertContains(resp, f'<strong>{title}</strong>')
        self.assertNotContains(resp, 'class="widget')
        self.assertNotContains(resp, 'Depends on')
        self.assertNotContains(resp, 'Good to see you')

    def test_cross_module_panels(self):
        carla = User.objects.get(username='carla')
        resp = self.client.get(reverse('users:detail', args=[carla.pk]))
        self.assertContains(resp, 'Open tasks')          # from tasks
        self.assertContains(resp, 'Recent stock moves')  # from stock
        task = Task.objects.get(title__startswith='Repair returned PDU')
        resp = self.client.get(reverse('tasks:detail', args=[task.pk]))
        self.assertContains(resp, 'Materials')
        self.assertContains(resp, 'DIN rail power supply 24 V 150 W')

    def test_set_status(self):
        task = Task.objects.get(title__startswith='Repair returned PDU')
        self.client.post(reverse('tasks:set_status', args=[task.pk]), {'status': 'done'})
        task.refresh_from_db()
        self.assertEqual(task.status, 'done')
        self.assertIsNotNone(task.completed_at)

    def test_gantt_bars(self):
        resp = self.client.get(reverse('tasks:gantt'))
        titles = [r['task'].title for r in resp.context['rows']]
        self.assertIn('Quarterly stock count', titles)
        self.assertIsNotNone(resp.context['today_left'])
        self.assertEqual(resp.context['window_start'].weekday(), 0)
        row = next(r for r in resp.context['rows'] if r['task'].title.startswith('Quarterly'))
        self.assertGreater(float(row['width']), 0)
        # A window far in the past contains nothing.
        resp = self.client.get(reverse('tasks:gantt') + '?start=2000-01-03')
        self.assertEqual(resp.context['rows'], [])

    def test_task_start_must_not_follow_due(self):
        resp = self.client.post(reverse('tasks:create'), {
            'title': 'Bad dates', 'status': 'todo', 'priority': 1,
            'start_date': '2026-10-10', 'due_date': '2026-10-01',
        })
        self.assertContains(resp, 'Due date cannot be before the start date.')

    def test_stock_moves_update_quantity(self):
        part = Part.objects.get(part_number='NUT-M3')
        where = StockItem.objects.get(part=part).location
        url = reverse('stock:move_create')
        self.client.post(url, {'part': part.pk, 'move_type': 'in', 'location': where.pk, 'quantity': '50'})
        self.assertEqual(on_hand(part), 200)
        self.client.post(url, {'part': part.pk, 'move_type': 'adjust', 'location': where.pk, 'quantity': '180'})
        self.assertEqual(on_hand(part), 180)
        self.assertEqual(part.moves.first().delta, -20)

    def test_cannot_issue_more_than_on_hand(self):
        part = Part.objects.get(part_number='PSU-PROG-3K')
        where = StockItem.objects.get(part=part).location
        resp = self.client.post(reverse('stock:move_create'), {'part': part.pk, 'move_type': 'out', 'location': where.pk, 'quantity': '5'})
        self.assertContains(resp, 'Only 1 pcs at')
        self.assertEqual(part.moves.count(), 1)

    def test_member_cannot_edit_other_users(self):
        self.client.login(username='bob', password='demo')
        alice = User.objects.get(username='alice')
        self.assertEqual(self.client.get(reverse('users:edit', args=[alice.pk])).status_code, 403)
        bob = User.objects.get(username='bob')
        self.assertEqual(self.client.get(reverse('users:edit', args=[bob.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('users:create')).status_code, 403)


# -- Images ----------------------------------------------------------------------------

import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image as PILImage

from core.models import Image
from devices.models import Device
from harness.models import HarnessProject


def picture(name='photo.jpg', size=(3000, 1500), fmt='JPEG', mode='RGB', orientation=None):
    img = PILImage.new(mode, size, (200, 30, 30, 128) if mode == 'RGBA' else (200, 30, 30))
    out = io.BytesIO()
    kwargs = {}
    if orientation:
        exif = PILImage.Exif()
        exif[0x0112] = orientation  # Orientation
        exif[0x010F] = 'CameraMaker'  # Make: metadata that must not survive
        kwargs['exif'] = exif
    img.save(out, fmt, **kwargs)
    return SimpleUploadedFile(name, out.getvalue(), content_type='image/' + fmt.lower())


class ImageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.client.login(username='admin', password='admin')
        self.part = Part.objects.get(part_number='PSU-24V-150W')

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def upload(self, record, *files, follow=False, **extra):
        url = reverse('core:image_upload', args=[record._meta.label_lower, record.pk])
        return self.client.post(url, {'images': list(files), **extra}, follow=follow)

    def test_upload_is_resized_turned_upright_and_stripped(self):
        response = self.upload(self.part, picture(orientation=6), caption='Label side', next='https://evil.example/')
        self.assertRedirects(response, self.part.get_absolute_url())  # unsafe "next" ignored
        image = self.part.images.get()
        self.assertEqual(image.caption, 'Label side')
        self.assertEqual(image.uploaded_by.username, 'admin')
        # 3000x1500 rotated a quarter turn by its EXIF orientation, then scaled to fit 2000.
        self.assertEqual((image.width, image.height), (1000, 2000))
        with PILImage.open(image.file.path) as stored:
            self.assertEqual(stored.format, 'JPEG')
            self.assertNotIn(0x010F, stored.getexif())
        with PILImage.open(image.thumb.path) as thumb:
            self.assertEqual(max(thumb.size), 400)

        full = self.client.get(image.url)
        self.assertEqual(full.status_code, 200)
        self.assertEqual(full['Content-Type'], 'image/jpeg')
        self.assertEqual(self.client.get(image.thumb_url).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(image.url).status_code, 302)  # login first

    def test_transparent_png_stays_png_and_bad_files_are_refused(self):
        self.upload(self.part, picture('logo.png', (64, 64), 'PNG', 'RGBA'),
                    SimpleUploadedFile('notes.jpg', b'not really a picture'))
        image = self.part.images.get()
        self.assertTrue(image.file.name.endswith('.png'))
        self.assertEqual((image.width, image.height), (64, 64))
        response = self.upload(self.part, SimpleUploadedFile('x.jpg', b'nope'), follow=True)
        self.assertContains(response, 'not an image Osmia can read')

    def test_only_models_with_images(self):
        budget_url = reverse('core:image_upload', args=['budgets.budget', 1])
        self.assertEqual(self.client.post(budget_url, {'images': [picture()]}).status_code, 404)
        self.assertEqual(self.client.post(reverse('core:image_upload', args=['nope.nothing', 1])).status_code, 404)

    def test_user_photos_are_theirs_and_managers(self):
        carla, bob = User.objects.get(username='carla'), User.objects.get(username='bob')
        self.client.login(username='bob', password='demo')
        self.assertEqual(self.upload(carla, picture()).status_code, 403)
        self.upload(bob, picture())
        self.assertEqual(bob.images.count(), 1)
        page = self.client.get(carla.get_absolute_url())
        self.assertNotContains(page, '+ Add images')
        self.assertEqual(self.client.post(reverse('core:image_update', args=[bob.images.get().pk]), {'action': 'delete'}).status_code, 302)
        self.client.login(username='admin', password='admin')
        self.upload(carla, picture())
        self.assertEqual(carla.images.count(), 1)

    def test_cover_caption_and_delete(self):
        self.upload(self.part, picture('a.jpg', (50, 50)), picture('b.jpg', (60, 60)))
        first, second = self.part.images.all()
        update = lambda img, **data: self.client.post(reverse('core:image_update', args=[img.pk]), data)
        update(second, action='cover')
        self.assertEqual(list(self.part.images.all()), [second, first])
        update(first, action='caption', caption='Side view')
        first.refresh_from_db()
        self.assertEqual(first.caption, 'Side view')
        path = first.file.path
        with self.captureOnCommitCallbacks(execute=True):  # files go once the delete is committed
            update(first, action='delete')
        self.assertFalse(Image.objects.filter(pk=first.pk).exists())
        self.assertFalse(Path(path).exists())

    def test_deleting_a_record_deletes_its_images(self):
        task = Task.objects.create(title='Photo task')
        self.upload(task, picture())
        path = task.images.get().file.path
        with self.captureOnCommitCallbacks(execute=True):
            task.delete()
        self.assertFalse(Image.objects.exists())
        self.assertFalse(Path(path).exists())

    def test_galleries_on_every_page(self):
        records = [
            self.part, StockItem.objects.filter(part=self.part).first().location, Task.objects.first(), User.objects.get(username='carla'), Device.objects.first(),
            Assembly.objects.first(), HarnessProject.objects.create(name='Bench harness'),
        ]
        for record in records:
            with self.subTest(record=record._meta.label):
                self.upload(record, picture(size=(80, 80)))
                page = reverse('harness:project_images', args=[record.pk]) if isinstance(record, HarnessProject) else record.get_absolute_url()
                self.assertContains(self.client.get(page), record.images.get().thumb_url)
        for url in (reverse('inventory:part_list'), reverse('stock:location_list'), reverse('users:list'), reverse('devices:list'),
                    reverse('assemblies:list'), reverse('harness:list')):
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), '?thumb=1')
        self.assertContains(self.client.get(reverse('harness:designer')), 'data-images-url="/harness/projects/0/images/"')
