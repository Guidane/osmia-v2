"""Every page of every installed module opens, for an administrator and for an
ordinary user, on the demo data. Errors (exceptions, 500s) fail the test, and so
does a page the administrator can't reach. See core/page_check.py."""
import shutil
import tempfile
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings

from core.page_check import pages
from osmia import addons


class AllPagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo', stdout=StringIO())

    def setUp(self):
        # The Modules pages read the add-on store and the data folder: use scratch ones, not the real ones.
        self.tmp = Path(tempfile.mkdtemp())
        self.override = override_settings(ADDONS=addons.Store(self.tmp), MEDIA_ROOT=self.tmp / 'media')
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, username, password):
        self.client.login(username=username, password=password)
        found = pages(self.client)  # an exception in any view fails the test right here
        self.assertGreater(len(found), 100)
        broken = [(name, url, status) for name, url, status in found if status >= 500]
        self.assertEqual(broken, [], f'Pages that fail for {username}')
        modules = {name.split(':')[0] for name, _, _ in found if ':' in name}
        self.assertTrue(set(settings.OSMIA_MODULES) - {'demo_data'} <= modules | {'demo_data'},
                        f'Modules without a page: {set(settings.OSMIA_MODULES) - modules}')
        return found

    def test_every_page_opens_for_an_administrator(self):
        found = self.check('admin', 'admin')
        unreachable = [(name, url, status) for name, url, status in found if status in (403, 404)]
        self.assertEqual(unreachable, [], 'Pages an administrator can\'t open')

    def test_every_page_opens_or_refuses_cleanly_for_a_user(self):
        self.check('bob', 'demo')  # may be refused (403, or sent elsewhere), but never an error
