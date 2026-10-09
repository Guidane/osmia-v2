"""Installing modules (osmia/addons.py), the Modules page and the setup page."""
import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core import setup
from osmia import addons


def make_zip(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return out.getvalue()


def manifest(label='widgets', **extra):
    return json.dumps({'label': label, 'title': label.title(), 'version': '1.0', **extra})


def module_files(label='widgets', prefix=None, **extra):
    prefix = f'{label}/' if prefix is None else prefix
    return {prefix + 'manifest.json': manifest(label, **extra), prefix + '__init__.py': '',
            prefix + 'apps.py': 'from core.modules import OsmiaModuleConfig\n'}


class ManifestTests(SimpleTestCase):
    def test_versions(self):
        self.assertTrue(addons.satisfies('2.0.0', '>=2.0'))
        self.assertTrue(addons.satisfies('2.1', '>=2.0,<3'))
        self.assertFalse(addons.satisfies('3.0', '>=2.0,<3'))
        self.assertTrue(addons.satisfies('1.0', ''))
        with self.assertRaises(addons.PackageError):
            addons.parse_version('1.x')

    def test_bad_manifests(self):
        for data, message in [
            ({'label': 'Bad-Label', 'title': 'X', 'version': '1'}, 'lowercase'),
            ({'label': 'ok', 'version': '1'}, 'title'),
            ({'label': 'ok', 'title': 'Ok', 'version': '1', 'depends': ['ok']}, 'itself'),
            ({'label': 'ok', 'title': 'Ok', 'version': '1', 'menu': [{'label': 'x', 'url': 'nocolon'}]}, 'menu'),
            ({'label': 'ok', 'title': 'Ok', 'version': '1', 'osmia': '>=99'}, 'needs Osmia'),
        ]:
            with self.subTest(message), self.assertRaisesMessage(addons.PackageError, message):
                addons.check_manifest(data)

    def test_bundled_modules_are_valid_and_ordered(self):
        bundled = addons.bundled_manifests()
        self.assertIn('tasks', bundled)
        order = addons.dependency_order(bundled)
        for label, m in bundled.items():
            for dep in m['depends']:
                if dep in bundled:
                    self.assertLess(order.index(dep), order.index(label), f'{dep} before {label}')

    def test_circular_dependencies(self):
        a = {'label': 'a', 'depends': ['b']}
        b = {'label': 'b', 'depends': ['a']}
        with self.assertRaisesMessage(addons.PackageError, 'Circular'):
            addons.dependency_order({'a': a, 'b': b})


class PackageTests(SimpleTestCase):
    def test_folder_or_top_level_layout(self):
        self.assertEqual(addons.read_package(make_zip(module_files())).label, 'widgets')
        package = addons.read_package(make_zip(module_files(prefix='')))
        self.assertEqual(package.label, 'widgets')
        self.assertIn('apps.py', package.files)

    def test_refused_packages(self):
        for files, message in [
            ({'a.txt': 'x'}, 'manifest.json'),
            ({**module_files(), 'widgets/../evil.py': 'x'}, 'unsafe path'),
            ({k: v for k, v in module_files().items() if not k.endswith('apps.py')}, 'apps.py'),
            ({'widgets/manifest.json': '{nope', 'widgets/__init__.py': '', 'widgets/apps.py': ''}, 'not valid JSON'),
            (module_files('core'), 'Osmia uses itself'),
        ]:
            with self.subTest(message), self.assertRaisesMessage(addons.PackageError, message):
                addons.read_package(make_zip(files))
        with self.assertRaisesMessage(addons.PackageError, 'not a .zip'):
            addons.read_package(b'hello')

    def test_extract_skips_caches(self):
        files = {**module_files(), 'widgets/__pycache__/x.pyc': 'junk', 'widgets/views.py': 'x = 1\n'}
        data = make_zip(files)
        package = addons.read_package(data)
        with tempfile.TemporaryDirectory() as tmp:
            addons.extract_package(data, package, Path(tmp) / 'widgets')
            self.assertTrue((Path(tmp) / 'widgets' / 'views.py').is_file())
            self.assertFalse((Path(tmp) / 'widgets' / '__pycache__').exists())


class ApplyTests(SimpleTestCase):
    """Applying the queue, with manage.py (migrate, check) stubbed out."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = addons.Store(self.tmp)
        self.store.root.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def stage(self, label='widgets', version='1.0', depends=()):
        staging = self.store.new_staging()
        for name, data in module_files(label, prefix='', version=version, depends=list(depends)).items():
            (staging / label).mkdir(parents=True, exist_ok=True)
            (staging / label / name).write_text(data, encoding='utf-8')
        return {'op': 'install', 'label': label, 'version': version, 'title': label.title(),
                'from_version': self.store.installed().get(label, {}).get('version'),
                'staging': staging.relative_to(self.store.root).as_posix()}

    def apply(self, ops, works=True):
        self.store.queue(ops, user='admin')
        with mock.patch.object(addons, '_manage', return_value=works) as manage:
            result = addons.apply_pending(self.store)
        return result, manage

    def test_install_upgrade_and_roll_back(self):
        (self.tmp / 'db.sqlite3').write_bytes(b'')  # an empty sqlite file is a valid empty database
        result, manage = self.apply([self.stage()])
        self.assertTrue(result['ok'], result['log'])
        self.assertEqual(self.store.installed()['widgets']['version'], '1.0')
        self.assertTrue((self.store.modules_dir / 'widgets' / 'apps.py').is_file())
        self.assertEqual([c.args[0][0] for c in manage.call_args_list], ['migrate', 'check'])
        self.assertTrue(result['backup'])
        self.assertIsNone(self.store.pending())

        result, _ = self.apply([self.stage(version='1.1')])
        self.assertEqual(result['changes'], ['Upgrade Widgets 1.0 → 1.1'])
        entry = self.store.installed()['widgets']
        self.assertEqual((entry['version'], entry['previous'][0]['version']), ('1.1', '1.0'))

        result, _ = self.apply([{'op': 'rollback', 'label': 'widgets'}])
        self.assertTrue(result['ok'], result['log'])
        self.assertEqual(self.store.installed()['widgets']['version'], '1.0')
        self.assertEqual(self.store.installed()['widgets']['previous'], [])

    def test_failure_puts_everything_back(self):
        self.apply([self.stage()])
        before = self.store.installed()
        result, _ = self.apply([self.stage(version='2.0')], works=False)
        self.assertFalse(result['ok'])
        self.assertIn('put back', result['log'])
        self.assertEqual(self.store.installed(), before)
        self.assertEqual(addons.read_manifest(self.store.modules_dir / 'widgets')['version'], '1.0')
        self.assertEqual(list(self.store.staging_dir.iterdir()), [])  # staging cleaned up
        self.assertEqual(self.store.results()[0]['id'], result['id'])

    def test_missing_dependency_fails(self):
        result, manage = self.apply([self.stage(depends=['gadgets'])])
        self.assertFalse(result['ok'])
        self.assertIn('needs gadgets', result['log'])
        self.assertEqual(self.store.installed(), {})
        manage.assert_not_called()

    def test_disable_enable_uninstall(self):
        self.apply([self.stage()])
        self.apply([{'op': 'disable', 'label': 'widgets'}])
        self.assertFalse(self.store.installed()['widgets']['enabled'])
        self.assertEqual(self.store.enabled_labels(), [])
        self.apply([{'op': 'enable', 'label': 'widgets'}])
        self.assertEqual(self.store.enabled_labels(), ['widgets'])
        result, manage = self.apply([{'op': 'uninstall', 'label': 'widgets', 'delete_data': True}])
        self.assertTrue(result['ok'])
        self.assertEqual(manage.call_args_list[0].args[0], ['migrate', 'widgets', 'zero', '--noinput'])
        self.assertNotIn('widgets', self.store.installed())
        self.assertFalse((self.store.modules_dir / 'widgets').exists())

    def test_start_migrates_when_new_migrations_arrive(self):
        (self.tmp / 'db.sqlite3').write_bytes(b'')
        self.apply([self.stage()])
        (self.store.modules_dir / 'widgets' / 'migrations').mkdir()

        def start():
            with mock.patch.object(addons, '_manage', return_value=True) as manage:
                addons.boot(self.store)
            return [c.args[0][0] for c in manage.call_args_list]
        with mock.patch.dict('os.environ', {'OSMIA_DEV_MODULES': ''}):
            self.assertEqual(start(), ['migrate'])  # first start with this marker
            self.assertEqual(start(), [])  # nothing new
            (self.store.modules_dir / 'widgets' / 'migrations' / '0001_initial.py').write_text('')  # e.g. after a git pull
            self.assertEqual(start(), ['migrate'])
            self.assertEqual(start(), [])

    def test_one_change_at_a_time(self):
        self.store.queue([{'op': 'enable', 'label': 'x'}])
        with self.assertRaisesMessage(addons.PackageError, 'already waiting'):
            self.store.queue([{'op': 'enable', 'label': 'x'}])
        self.store.cancel_pending()
        self.assertIsNone(self.store.pending())


class ModulesPageTests(TestCase):
    """The Modules page as on a real install: changes are queued in a scratch store."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('admin', 'a@example.com', 'admin')
        get_user_model().objects.create_user('bob', password='demo', is_staff=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = addons.Store(self.tmp)
        self.override = override_settings(ADDONS=self.store, OSMIA_DEV=False)
        self.override.enable()
        self.client.login(username='admin', password='admin')

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install_state(self, **modules):
        """Pretend these bundled modules are installed: {label: enabled}."""
        bundled = addons.bundled_manifests()
        self.store.save_state({'modules': {l: {**bundled[l], 'enabled': on, 'previous': []} for l, on in modules.items()}})

    def test_superusers_only(self):
        self.client.login(username='bob', password='demo')
        self.assertEqual(self.client.get(reverse('core:modules')).status_code, 302)
        self.assertEqual(self.client.post(reverse('core:modules_install'), {'module': 'tools'}).status_code, 302)
        self.assertIsNone(self.store.pending())

    def test_page_lists_available_modules(self):
        resp = self.client.get(reverse('core:modules'))
        self.assertContains(resp, 'name="module" value="orders"')
        self.assertContains(resp, 'Upload a module')

    def test_install_brings_its_dependencies(self):
        resp = self.client.post(reverse('core:modules_install'), {'module': 'orders', 'demo': '1'})
        self.assertRedirects(resp, reverse('core:modules_applying'))
        job = self.store.pending()
        labels = [op['label'] for op in job['ops']]
        self.assertEqual(labels[-1], 'orders')
        for dep in ('departments', 'tasks', 'tools', 'inventory', 'stock', 'budgets', 'vendors'):
            self.assertIn(dep, labels)
        self.assertLess(labels.index('inventory'), labels.index('stock'))
        self.assertEqual(job['demo'], labels)
        for op in job['ops']:
            self.assertTrue((self.store.root / op['staging'] / op['label'] / 'manifest.json').is_file())
        self.assertContains(self.client.get(reverse('core:modules_applying')), 'Install Orders')
        self.assertEqual(self.client.get(reverse('core:modules_status')).json()['pending'], True)

        # One change at a time; cancelling clears the staged files.
        self.client.post(reverse('core:modules_install'), {'module': 'tools'})
        self.assertEqual(len(self.store.pending()['ops']), len(labels))
        self.client.post(reverse('core:modules_cancel'))
        self.assertIsNone(self.store.pending())
        self.assertEqual(list(self.store.staging_dir.iterdir()), [])

    def test_upload(self):
        self.install_state(tools=True)
        data = addons.zip_folder(addons.BUNDLED_DIR / 'vendors')
        self.client.post(reverse('core:modules_upload'), {'package': SimpleUploadedFile('vendors.zip', data)})
        ops = self.store.pending()['ops']
        self.assertEqual([(o['op'], o['label']) for o in ops], [('install', 'vendors')])
        self.assertTrue((self.store.root / ops[0]['staging'] / 'vendors' / 'models.py').is_file())

    def test_upload_refusals(self):
        self.install_state(tools=True)
        for files, message in [
            (module_files('json'), 'already the name of a Python package'),
            (module_files('widgets', depends=['nothing_here']), 'Osmia doesn'),
            (module_files('widgets', requires=['surely-not-installed-pkg']), 'surely-not-installed-pkg'),
            (module_files('tools', version='0.5'), 'Roll back'),
        ]:
            with self.subTest(message):
                resp = self.client.post(reverse('core:modules_upload'),
                                        {'package': SimpleUploadedFile('m.zip', make_zip(files))}, follow=True)
                self.assertContains(resp, message)
                self.assertIsNone(self.store.pending())

    def test_dependents_block_disable_and_uninstall(self):
        self.install_state(departments=True, tasks=True)
        resp = self.client.get(reverse('core:modules_change', args=['departments', 'disable']))
        self.assertContains(resp, 'Tasks needs Departments')
        self.client.post(reverse('core:modules_change', args=['departments', 'uninstall']))
        self.assertIsNone(self.store.pending())
        self.client.post(reverse('core:modules_change', args=['tasks', 'uninstall']), {'delete_data': '1'})
        self.assertEqual(self.store.pending()['ops'], [{'op': 'uninstall', 'label': 'tasks', 'title': 'Tasks', 'delete_data': True}])

    def test_enable_needs_dependencies_on(self):
        self.install_state(departments=False, tasks=False)
        resp = self.client.get(reverse('core:modules_change', args=['tasks', 'enable']))
        self.assertContains(resp, 'Enable it first')
        self.client.post(reverse('core:modules_change', args=['departments', 'enable']))
        self.assertEqual(self.store.pending()['ops'][0]['op'], 'enable')

    def test_updates_offered(self):
        self.install_state(tools=True)
        state = self.store.state()
        state['modules']['tools']['version'] = '0.9'
        self.store.save_state(state)
        self.assertContains(self.client.get(reverse('core:modules')), 'Update to 1.0.0')
        self.client.post(reverse('core:modules_update_all'))
        op = self.store.pending()['ops'][0]
        self.assertEqual((op['label'], op['from_version'], op['version']), ('tools', '0.9', '1.0.0'))

    def test_result_after_restart(self):
        self.store._add_result({'id': 'abc', 'ok': False, 'changes': ['Install Tools 1.0.0'], 'log': 'FAILED: boom',
                                'finished_at': '2026-10-07T10:00:00', 'seconds': 1.0, 'requested_by': 'admin'})
        resp = self.client.get(reverse('core:modules') + '?done=abc')
        self.assertContains(resp, 'That change failed')
        self.assertContains(resp, 'FAILED: boom')

    def test_download(self):
        self.install_state(tools=True)
        shutil.copytree(addons.BUNDLED_DIR / 'tools', self.store.modules_dir / 'tools')
        resp = self.client.get(reverse('core:modules_download', args=['tools']))
        self.assertEqual(resp['Content-Disposition'], 'attachment; filename="tools-1.0.0.zip"')
        self.assertEqual(addons.read_package(resp.content).label, 'tools')
        self.assertEqual(self.client.get(reverse('core:modules_download', args=['nope'])).status_code, 404)

    def test_development_mode_is_read_only(self):
        with override_settings(OSMIA_DEV=True):
            resp = self.client.get(reverse('core:modules'))
            self.assertContains(resp, 'OSMIA_DEV_MODULES')
            self.assertNotContains(resp, 'Upload a module')
            self.client.post(reverse('core:modules_change', args=['tools', 'disable']))
        self.assertIsNone(self.store.pending())


class SetupTests(TestCase):
    def setUp(self):
        setup._ready = False

    def tearDown(self):
        setup._ready = False

    def test_every_page_goes_to_setup_until_there_is_an_admin(self):
        for url in (reverse('core:home'), reverse('users:login'), reverse('tasks:board')):
            self.assertRedirects(self.client.get(url), reverse('core:setup'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('core:setup')).status_code, 200)

    def test_setup_makes_the_admin_and_loads_demo_data(self):
        resp = self.client.post(reverse('core:setup'), {
            'username': 'boss', 'first_name': 'Pat', 'password1': 'a-long-passphrase-1', 'password2': 'a-long-passphrase-1',
            'demo': 'on',
        })
        self.assertRedirects(resp, reverse('core:home'))
        boss = get_user_model().objects.get(username='boss')
        self.assertTrue(boss.is_superuser and boss.is_staff)
        self.assertTrue(get_user_model().objects.filter(username='alice').exists())
        self.assertFalse(get_user_model().objects.filter(username='admin').exists())  # no default password
        self.assertEqual(self.client.get(reverse('core:home')).status_code, 200)  # logged in
        self.assertRedirects(self.client.get(reverse('core:setup')), reverse('core:home'))

    def test_setup_queues_the_chosen_modules(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        store = addons.Store(tmp)
        with override_settings(ADDONS=store, OSMIA_DEV=False):
            self.assertContains(self.client.get(reverse('core:setup')), 'value="harness"')
            resp = self.client.post(reverse('core:setup'), {
                'username': 'boss', 'password1': 'a-long-passphrase-1', 'password2': 'a-long-passphrase-1',
                'modules': ['tasks'], 'demo': 'on',
            })
        self.assertRedirects(resp, reverse('core:modules_applying'), fetch_redirect_response=False)
        job = store.pending()
        self.assertEqual([op['label'] for op in job['ops']], ['departments', 'tasks'])
        self.assertEqual(job['demo'], ['users', 'departments', 'tasks'])
        self.assertEqual(job['requested_by'], 'boss')


# -- Moving all the data to another Osmia ------------------------------------------

import sqlite3  # noqa: E402


def make_db(path, value):
    db = sqlite3.connect(path)
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS t (v TEXT)')
            db.execute('DELETE FROM t')
            db.execute('INSERT INTO t VALUES (?)', (value,))
    finally:
        db.close()


def db_value(path):
    db = sqlite3.connect(path)
    try:
        return db.execute('SELECT v FROM t').fetchone()[0]
    finally:
        db.close()


class DataMoveTests(SimpleTestCase):
    """Exporting one Osmia's data and importing it into another (manage.py stubbed out)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dev = addons.Store(self.tmp / 'dev')   # where the data comes from
        self.prod = addons.Store(self.tmp / 'prod')  # where it goes
        for store, name in ((self.dev, 'dev'), (self.prod, 'prod')):
            store.root.mkdir(parents=True)
            make_db(store.db_file, name)
            media = store.data_dir / 'media' / 'images' / '2026' / '10'
            media.mkdir(parents=True)
            (media / f'{name}.pdf').write_bytes(b'%PDF-' + name.encode())
        self.add_module(self.dev, 'widgets')
        (self.dev.modules_dir / 'widgets' / '__pycache__').mkdir()
        (self.dev.modules_dir / 'widgets' / '__pycache__' / 'x.pyc').write_bytes(b'')
        self.add_module(self.prod, 'gadgets')  # prod's own module, replaced by the import

    def add_module(self, store, label):
        for name, data in module_files(label, prefix='').items():
            (store.modules_dir / label).mkdir(parents=True, exist_ok=True)
            (store.modules_dir / label / name).write_text(data, encoding='utf-8')
        entry = {**addons.check_manifest(json.loads(manifest(label))), 'enabled': True, 'previous': [{'version': '0.9'}]}
        store.save_state({'modules': {label: entry}})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def export(self):
        path = self.tmp / 'export.zip'
        addons.export_data(self.dev, path, self.dev.data_dir / 'media', self.dev.installed(), self.dev.modules_dir, user='ann')
        return path

    def queue_import(self, path):
        info = addons.read_export(path)
        staging = self.prod.new_staging()
        addons.extract_export(path, staging)
        self.prod.queue([{'op': 'import', 'label': '', 'source': 'the dev data', 'media_root': str(self.prod.data_dir / 'media'),
                          'staging': staging.relative_to(self.prod.root).as_posix()}], user='admin')
        return info

    def test_export_holds_everything(self):
        with zipfile.ZipFile(self.export()) as zf:
            names = set(zf.namelist())
            info = json.loads(zf.read(addons.DATA_INFO))
        self.assertEqual(names, {'osmia-data.json', 'db.sqlite3', 'media/images/2026/10/dev.pdf',
                                 'modules/widgets/manifest.json', 'modules/widgets/__init__.py', 'modules/widgets/apps.py'})
        self.assertEqual((info['exported_by'], info['files'], list(info['modules'])), ('ann', 1, ['widgets']))
        self.assertNotIn('previous', info['modules']['widgets'])  # rollbacks belong to the old server
        sizes = addons.export_sizes(self.dev, self.dev.data_dir / 'media', self.dev.installed(), self.dev.modules_dir)
        module_bytes = sum(p.stat().st_size for p in (self.dev.modules_dir / 'widgets').glob('*.*'))  # not the __pycache__
        self.assertEqual((sizes['file_count'], sizes['files'], sizes['modules']), (1, 8, module_bytes))
        self.assertEqual(sizes['total'], self.dev.db_file.stat().st_size + 8 + module_bytes)

    def test_import_replaces_everything(self):
        self.queue_import(self.export())
        with mock.patch.object(addons, '_manage', return_value=True) as manage:
            result = addons.apply_pending(self.prod)
        self.assertTrue(result['ok'], result['log'])
        self.assertEqual(result['changes'], ['Replace all data with the dev data'])
        self.assertEqual([c.args[0][0] for c in manage.call_args_list], ['migrate', 'check'])
        self.assertEqual(db_value(self.prod.db_file), 'dev')
        media = self.prod.data_dir / 'media' / 'images' / '2026' / '10'
        self.assertEqual([p.name for p in media.iterdir()], ['dev.pdf'])
        self.assertEqual(list(self.prod.installed()), ['widgets'])
        self.assertEqual(self.prod.installed()['widgets']['previous'], [])
        self.assertTrue((self.prod.modules_dir / 'widgets' / 'apps.py').is_file())
        self.assertFalse((self.prod.modules_dir / 'gadgets').exists())
        # What prod had is kept: its database backup, files and modules.
        self.assertEqual(db_value(self.prod.backups_dir / result['backup']), 'prod')
        self.assertTrue(list(self.prod.backups_dir.glob('media-before-import-*/images/2026/10/prod.pdf')))
        self.assertTrue(list(self.prod.previous_dir.glob('before-import-*/gadgets/apps.py')))
        self.assertEqual(list(self.prod.staging_dir.iterdir()), [])

    def test_failed_import_is_undone(self):
        self.queue_import(self.export())
        with mock.patch.object(addons, '_manage', return_value=False):
            result = addons.apply_pending(self.prod)
        self.assertFalse(result['ok'])
        self.assertEqual(db_value(self.prod.db_file), 'prod')
        self.assertTrue((self.prod.data_dir / 'media' / 'images' / '2026' / '10' / 'prod.pdf').is_file())
        self.assertEqual(list(self.prod.installed()), ['gadgets'])
        self.assertTrue((self.prod.modules_dir / 'gadgets' / 'apps.py').is_file())
        self.assertFalse((self.prod.modules_dir / 'widgets').exists())

    def test_refused_exports(self):
        info = lambda **extra: json.dumps({'format': 1, 'osmia': addons.OSMIA_VERSION, 'modules': {}, **extra})
        good = {addons.DATA_INFO: info(), 'db.sqlite3': ''}
        for files, message in [
            (module_files(), 'not an Osmia data export'),
            ({**good, addons.DATA_INFO: info(osmia='99.0')}, 'Update this Osmia first'),
            ({addons.DATA_INFO: info()}, 'no database'),
            ({**good, '../evil.py': ''}, 'unsafe path'),
            ({**good, 'settings.py': ''}, "isn't Osmia data"),
            ({**good, addons.DATA_INFO: info(modules={'widgets': {}})}, "doesn't contain it"),
        ]:
            with self.subTest(message):
                with self.assertRaisesMessage(addons.PackageError, message):
                    addons.read_export(io.BytesIO(make_zip(files)))


class DataMovePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'a@example.com', 'admin')
        get_user_model().objects.create_user('bob', password='demo', is_staff=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = addons.Store(self.tmp)
        self.store.root.mkdir(parents=True)
        make_db(self.store.db_file, 'here')
        (self.tmp / 'media').mkdir()
        (self.tmp / 'media' / 'a.jpg').write_bytes(b'jpeg')
        self.override = override_settings(ADDONS=self.store, OSMIA_DEV=False, MEDIA_ROOT=self.tmp / 'media')
        self.override.enable()
        self.client.login(username='admin', password='admin')

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_and_import(self):
        self.assertContains(self.client.get(reverse('core:modules')), 'Export all data')
        resp = self.client.get(reverse('core:data_export'))  # the overview first
        db_size = self.store.db_file.stat().st_size
        self.assertEqual(resp.context['sizes'], {'database': db_size, 'files': 4, 'file_count': 1, 'modules': 0, 'total': db_size + 4})
        self.assertEqual(resp.context['need_there'], 2 * (db_size + 4))
        self.assertContains(resp, '1 file)')
        self.assertContains(resp, reverse('core:data_export_download'))
        with mock.patch('shutil.disk_usage', return_value=mock.Mock(free=10)):
            resp = self.client.get(reverse('core:data_export'))
        self.assertContains(resp, "isn't enough free space")
        self.assertNotContains(resp, reverse('core:data_export_download'))

        resp = self.client.get(reverse('core:data_export_download'))
        self.assertTrue(resp['Content-Disposition'].startswith('attachment; filename="osmia-data-'))
        data = b''.join(resp.streaming_content)
        self.assertEqual(addons.read_export(io.BytesIO(data))['exported_by'], 'admin')
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.assertEqual(zf.read('media/a.jpg'), b'jpeg')

        def upload(**extra):
            return self.client.post(reverse('core:data_import'), {'export': SimpleUploadedFile('d.zip', data), **extra}, follow=True)
        self.assertContains(upload(), 'Tick the box')
        self.assertIsNone(self.store.pending())
        self.assertRedirects(upload(confirm='1'), reverse('core:modules_applying'))
        op = self.store.pending()['ops'][0]
        self.assertEqual((op['op'], op['media_root']), ('import', str(self.tmp / 'media')))
        self.assertTrue((self.store.root / op['staging'] / 'media' / 'a.jpg').is_file())

    def test_refusals(self):
        resp = self.client.post(reverse('core:data_import'), {'export': SimpleUploadedFile('m.zip', make_zip(module_files())),
                                                              'confirm': '1'}, follow=True)
        self.assertContains(resp, 'not an Osmia data export')
        self.assertIsNone(self.store.pending())
        self.assertEqual(list(self.store.staging_dir.glob('*')), [])
        self.client.login(username='bob', password='demo')
        self.assertEqual(self.client.get(reverse('core:data_export')).status_code, 302)
        self.assertEqual(self.client.get(reverse('core:data_export_download')).status_code, 302)
        with override_settings(OSMIA_DEV=True):  # running from source: export only
            self.client.login(username='admin', password='admin')
            page = self.client.get(reverse('core:modules'))
            self.assertContains(page, 'Export all data')
            self.assertNotContains(page, 'Import and replace')
