"""Osmia in a real browser (Playwright): every page without JavaScript errors, and
the main interactions clicked through. Skipped when Playwright or a browser isn't
there (pip install -r requirements-dev.txt; it uses Edge or Chrome if installed,
else the browser from ``playwright install chromium``)."""
import io
import os
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest import SkipTest

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.test import Client, override_settings, tag
from PIL import Image as PILImage

from core.page_check import pages
from osmia import addons

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # optional: only needed for these tests
    sync_playwright = None


def launch(playwright):
    for options in ({'channel': 'msedge'}, {'channel': 'chrome'}, {}):
        try:
            return playwright.chromium.launch(**options)
        except Exception:
            continue
    raise SkipTest('No browser for Playwright (install Edge or Chrome, or run "playwright install chromium").')


@tag('browser')
class BrowserTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise SkipTest('Playwright is not installed (pip install -r requirements-dev.txt).')
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'  # Playwright's event loop runs alongside the ORM
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = launch(cls.playwright)
        except SkipTest:
            cls.playwright.stop()
            super().tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        call_command('load_demo', stdout=StringIO())
        self.tmp = Path(tempfile.mkdtemp())
        self.override = override_settings(ADDONS=addons.Store(self.tmp), MEDIA_ROOT=self.tmp / 'media')
        self.override.enable()
        self.page = self.browser.new_page()
        self.page.set_default_timeout(15_000)
        self.errors = []
        self.page.on('pageerror', lambda exc: self.errors.append(f'{self.page.url}: {exc}'))
        # Console errors, except "Failed to load resource": the response handler names the file.
        self.page.on('console', lambda msg: msg.type == 'error' and not msg.text.startswith('Failed to load resource')
                     and self.errors.append(f'{self.page.url}: console: {msg.text}'))
        self.page.on('response', lambda r: (r.status >= 500 or (r.status == 404 and r.request.resource_type != 'document'))
                     and self.errors.append(f'{r.url} (from {self.page.url}): HTTP {r.status}'))

    def tearDown(self):
        self.page.close()
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def url(self, path):
        return self.live_server_url + path

    def log_in(self, username='admin', password='admin'):
        self.page.goto(self.url('/users/login/'))
        self.page.fill('input[name=username]', username)
        self.page.fill('input[name=password]', password)
        self.page.click('button[type=submit], form button')
        self.page.wait_for_url(lambda url: '/login' not in url)

    def test_every_page_without_javascript_errors(self):
        client = Client()
        client.login(username='admin', password='admin')
        def html_page(url):  # not downloads (.zip, files) or JSON
            r = client.get(url)
            return 'text/html' in r.get('Content-Type', '') and not r.has_header('Content-Disposition')
        urls = [url for name, url, status in pages(client) if status == 200 and html_page(url)]
        self.assertGreater(len(urls), 90)
        self.log_in()
        for url in urls:
            self.page.goto(self.url(url), wait_until='load')
        self.assertEqual(self.errors, [])

    def test_projects_tasks_and_files(self):
        from projects.models import Project
        pdu, bench = Project.objects.get(name='PDU-100 production run'), Project.objects.get(name='Automatic test bench')
        self.log_in()

        # A new task from the project page lands in its task list.
        self.page.goto(self.url(bench.get_absolute_url()))
        self.page.fill('input[name=title]', 'Order the bench frame')
        self.page.click('text=Add task')
        expect(self.page.locator('.flash').first).to_contain_text('charged to its budget')
        self.page.click('a:text("Order the bench frame")')

        # On the task's page, move it to another project.
        panel = self.page.locator('section.panel', has=self.page.locator('h2:text-is("Project")'))
        expect(panel).to_contain_text(bench.name)
        panel.locator('select[name=project]').select_option(str(pdu.pk))
        panel.locator('button').click()
        expect(self.page.locator('.flash').first).to_contain_text(f'Task moved to {pdu}')
        expect(panel).to_contain_text(pdu.name)

        # Files: a PDF opens in the viewer, a picture as a picture.
        self.page.goto(self.url(pdu.get_absolute_url()))
        pdf = {'name': 'Datasheet.pdf', 'mimeType': 'application/pdf',
               'buffer': b'%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n'}
        self.page.set_input_files('.gallery input[type=file]', pdf)
        expect(self.page.locator('.gallery-file .file-ext')).to_have_text('PDF')
        out = io.BytesIO()
        PILImage.new('RGB', (40, 30), (20, 120, 200)).save(out, 'PNG')
        self.page.set_input_files('.gallery input[type=file]', {'name': 'front.png', 'mimeType': 'image/png', 'buffer': out.getvalue()})
        expect(self.page.locator('.gallery-item')).to_have_count(2)

        self.page.click('.gallery-file')
        box = self.page.locator('dialog.lightbox')
        expect(box).to_be_visible()
        expect(box.locator('.lightbox-doc')).to_be_visible()
        self.assertTrue(box.locator('.lightbox-doc').get_attribute('src'))
        expect(box.locator('.lightbox-download')).to_be_visible()
        expect(box.locator('.lightbox-cover')).to_be_hidden()  # a PDF can't be the cover
        self.page.keyboard.press('ArrowRight')
        expect(box.locator('.lightbox-stage img')).to_be_visible()
        expect(box.locator('.lightbox-doc')).to_be_hidden()
        expect(box.locator('.lightbox-cover')).to_be_visible()
        box.locator('.lightbox-close').click()
        expect(box).to_be_hidden()
        self.assertEqual(self.errors, [])

    def test_export_overview(self):
        import sqlite3
        sqlite3.connect(self.tmp / 'db.sqlite3').close()  # the scratch data folder's database
        self.log_in()
        self.page.goto(self.url('/modules/'))
        self.page.click('text=Export all data')
        expect(self.page.locator('h1')).to_have_text('Export all data')
        expect(self.page.locator('table.list').first).to_contain_text('Total')
        with self.page.expect_download() as download:
            self.page.click('text=Download the export')
        path = download.value.path()
        self.assertEqual(addons.read_export(path)['exported_by'], 'admin')
        self.assertEqual(self.errors, [])
