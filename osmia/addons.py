"""
Installing, upgrading and removing Osmia modules from the website.

A module is a Django app with a ``manifest.json``, uploaded as a ``.zip``.
Django can't add or drop apps while it runs, so the website never changes
the module set itself. It *queues* the change (``pending.json``) and restarts;
on the way up, before Django loads, ``boot()`` applies the queue:

1. back up the database,
2. move the module folders into place and update ``installed.json``,
3. run ``manage.py migrate`` and ``manage.py check`` in a subprocess,
4. and if any of that fails, put the folders, ``installed.json`` and the
   database back as they were. The outcome is kept in ``results.json`` for the
   Modules page.

Nothing here imports Django, so the launcher (``serve.py``) and ``settings.py``
can use it too. Everything lives under the data folder::

    data/
      db.sqlite3
      media/                      uploaded images
      backups/                    database backups, made before every change
      addons/
        installed.json            which modules are installed, their versions, on/off
        modules/<label>/          the installed modules (on sys.path)
        previous/<label>/<ver>/   the version an upgrade replaced, for rolling back
        staging/<id>/<label>/     uploads waiting to be installed
        pending.json              the queued change
        results.json              what the last changes did

The same queue moves a whole Osmia to another server: ``export_data()``
packs the database, the uploaded files and the installed modules into one
``.zip``, and an ``import`` op swaps them in on the other side.
"""
import io
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from importlib.util import find_spec
from pathlib import Path

OSMIA_VERSION = '2.0.0'
BASE_DIR = Path(__file__).resolve().parent.parent
BUNDLED_DIR = BASE_DIR / 'modules'  # the modules that come with Osmia, as source
BUILTIN = ('users',)  # always installed; not a package

# Names a module can't take: Osmia's own packages and Django's apps.
RESERVED = {
    'core', 'users', 'osmia', 'modules', 'data', 'static', 'media', 'tests', 'test', 'setup',
    'admin', 'auth', 'contenttypes', 'sessions', 'messages', 'staticfiles', 'humanize', 'django', 'waitress',
}
LABEL_RE = re.compile(r'^[a-z][a-z0-9_]{1,39}$')
VERSION_RE = re.compile(r'^\d+(\.\d+){0,3}$')

MAX_PACKAGE_BYTES = 100 * 1024 * 1024  # unpacked
MAX_PACKAGE_FILES = 5000
SKIPPED = ('__pycache__/', '__MACOSX/', '.git/')
KEEP_BACKUPS = 20
KEEP_RESULTS = 30
RESTART_EXIT_CODE = 3  # serve.py and Django's runserver both restart on it

# Data exports (moving an Osmia to another server)
DATA_INFO = 'osmia-data.json'
DATA_FORMAT = 1
MAX_DATA_BYTES = 50 * 1024 ** 3  # unpacked
MAX_DATA_FILES = 1_000_000
STORED_AS_IS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.zip', '.7z', '.docx', '.xlsx', '.pptx'}  # compressed already


class PackageError(Exception):
    """A module package that can't be installed; the message says why."""


# -- Versions ----------------------------------------------------------------------

def parse_version(text):
    if not VERSION_RE.match(str(text or '')):
        raise PackageError(f'"{text}" is not a version number (use e.g. 1.0 or 1.2.3).')
    parts = tuple(int(p) for p in str(text).split('.'))
    return parts + (0,) * (4 - len(parts))


def satisfies(version, spec):
    """Does ``version`` meet ``spec``, e.g. ``">=2.0"`` or ``">=2.0,<3"``? Empty means any."""
    have = parse_version(version)
    for clause in filter(None, (c.strip() for c in str(spec or '').split(','))):
        m = re.match(r'^(>=|<=|==|>|<)?\s*(.+)$', clause)
        op, want = m.group(1) or '==', parse_version(m.group(2))
        ok = {'>=': have >= want, '<=': have <= want, '==': have == want, '>': have > want, '<': have < want}[op]
        if not ok:
            return False
    return True


# -- Manifests ---------------------------------------------------------------------

def check_manifest(m, label=None):
    """Raise PackageError unless ``m`` is a usable manifest. Returns it tidied."""
    if not isinstance(m, dict):
        raise PackageError('manifest.json must hold a JSON object.')
    m = dict(m)
    m['label'] = m.get('label') or label
    if label and m['label'] != label:
        raise PackageError(f'The folder is "{label}" but manifest.json says the label is "{m["label"]}".')
    if not isinstance(m['label'], str) or not LABEL_RE.match(m['label']):
        raise PackageError('The label must be 2-40 lowercase letters, digits or _, starting with a letter.')
    if m['label'] in RESERVED:
        raise PackageError(f'"{m["label"]}" is a name Osmia uses itself; give the module another label.')
    if not isinstance(m.get('title'), str) or not m['title'].strip():
        raise PackageError('manifest.json needs a "title".')
    parse_version(m.get('version'))
    m['depends'] = list(m.get('depends') or [])
    if not all(isinstance(d, str) and LABEL_RE.match(d) for d in m['depends']):
        raise PackageError('"depends" must be a list of module labels.')
    if m['label'] in m['depends']:
        raise PackageError('A module can\'t depend on itself.')
    m['requires'] = list(m.get('requires') or [])
    menu = m.get('menu') or []
    if not isinstance(menu, list) or not all(
            isinstance(e, dict) and isinstance(e.get('label'), str) and ':' in str(e.get('url', '')) for e in menu):
        raise PackageError('"menu" must be a list of {"label": ..., "url": "<label>:<url name>"}.')
    m['menu'] = menu
    try:
        m['sequence'] = int(m.get('sequence', 100))
    except (TypeError, ValueError):
        raise PackageError('"sequence" must be a number.')
    if m.get('osmia'):
        try:
            ok = satisfies(OSMIA_VERSION, m['osmia'])
        except (PackageError, AttributeError):
            raise PackageError(f'"osmia" must be a version requirement like ">=2.0", not "{m["osmia"]}".')
        if not ok:
            raise PackageError(f'{m["title"]} needs Osmia {m["osmia"]}; this is Osmia {OSMIA_VERSION}.')
    return m


def read_manifest(folder):
    folder = Path(folder)
    try:
        data = json.loads((folder / 'manifest.json').read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        raise PackageError(f'{folder.name} has no manifest.json.')
    except ValueError as exc:
        raise PackageError(f'manifest.json of {folder.name} is not valid JSON: {exc}')
    return check_manifest(data, folder.name)


def dependency_order(manifests):
    """Labels of ``{label: manifest}`` with every module after its dependencies."""
    ordered, seen = [], set()

    def visit(label, stack):
        if label in seen or label not in manifests:
            return
        if label in stack:
            raise PackageError('Circular dependency: ' + ' -> '.join(stack + (label,)))
        for dep in manifests[label].get('depends', ()):
            visit(dep, stack + (label,))
        seen.add(label)
        ordered.append(label)

    for label in sorted(manifests, key=lambda l: (manifests[l].get('sequence', 100), l)):
        visit(label, ())
    return ordered


def bundled_manifests():
    """{label: manifest} of the modules shipped in ``modules/``."""
    found = {}
    if BUNDLED_DIR.is_dir():
        for folder in sorted(BUNDLED_DIR.iterdir()):
            if (folder / 'manifest.json').is_file():
                found[folder.name] = read_manifest(folder)
    return found


def missing_requirements(manifest):
    """Python packages the module needs that aren't installed on the server."""
    return [r for r in manifest.get('requires', []) if find_spec(re.split(r'[<>=!\[ ]', r)[0].replace('-', '_')) is None]


# -- Packages (.zip) ---------------------------------------------------------------

@dataclass
class Package:
    manifest: dict
    files: dict = field(default_factory=dict)  # {path inside the module: zip member name}

    @property
    def label(self):
        return self.manifest['label']


def read_package(data):
    """Check an uploaded module ``.zip`` (bytes or a file) and return its Package.

    The zip holds the module folder (``tools/manifest.json``, ``tools/apps.py``, ...)
    or the module's files at the top level.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data) if isinstance(data, bytes) else data)
    except zipfile.BadZipFile:
        raise PackageError('That is not a .zip file.')
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        names = {}
        for info in infos:
            name = info.filename.replace('\\', '/')
            parts = name.split('/')
            if name.startswith('/') or '..' in parts or ':' in parts[0]:
                raise PackageError(f'The zip contains an unsafe path: {info.filename}')
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise PackageError(f'The zip contains a link, which isn\'t allowed: {info.filename}')
            if any(s in name + '/' for s in SKIPPED) or name.endswith(('.pyc', '.DS_Store')) or parts[-1] == 'Thumbs.db':
                continue
            names[name] = info
        if len(names) > MAX_PACKAGE_FILES:
            raise PackageError(f'The zip holds more than {MAX_PACKAGE_FILES} files.')
        if sum(i.file_size for i in names.values()) > MAX_PACKAGE_BYTES:
            raise PackageError(f'The module is larger than {MAX_PACKAGE_BYTES // (1024 * 1024)} MB unpacked.')

        if 'manifest.json' in names:
            prefix, folder = '', None
        else:
            tops = {n.split('/')[0] for n in names}
            if len(tops) != 1 or f'{next(iter(tops))}/manifest.json' not in names:
                raise PackageError('The zip needs a manifest.json, at the top or in one module folder.')
            folder = next(iter(tops))
            prefix = folder + '/'
        try:
            manifest = json.loads(zf.read(names[prefix + 'manifest.json']).decode('utf-8-sig'))
        except ValueError as exc:
            raise PackageError(f'manifest.json is not valid JSON: {exc}')
        manifest = check_manifest(manifest, folder)
        files = {n[len(prefix):]: n for n in names}
        for required in ('__init__.py', 'apps.py'):
            if required not in files:
                raise PackageError(f'The module has no {required}, so it isn\'t a Django app.')
        return Package(manifest, files)


def extract_package(data, package, dest):
    """Unpack ``package`` (read from ``data``) into the folder ``dest``."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    with zipfile.ZipFile(io.BytesIO(data) if isinstance(data, bytes) else data) as zf:
        for rel, member in package.files.items():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, 'wb') as out:
                while chunk := src.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_PACKAGE_BYTES:  # sizes in a zip can lie
                        raise PackageError('The module is larger than allowed unpacked.')
                    out.write(chunk)


def zip_folder(folder):
    """A module folder as ``.zip`` bytes, laid out as ``<label>/...``."""
    folder = Path(folder)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob('*')):
            rel = path.relative_to(folder).as_posix()
            if path.is_dir() or any(s in rel + '/' for s in SKIPPED) or rel.endswith('.pyc'):
                continue
            zf.write(path, f'{folder.name}/{rel}')
    return out.getvalue()


# -- Files -------------------------------------------------------------------------

def _rmtree(path):
    def make_writable(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if Path(path).exists():
        shutil.rmtree(path, onerror=make_writable)


def _now():
    return datetime.now().isoformat(timespec='seconds')


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return default


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)


# -- The store ---------------------------------------------------------------------

class Store:
    """The installed modules and the queue of changes, under ``data_dir``."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / 'addons'
        self.modules_dir = self.root / 'modules'
        self.previous_dir = self.root / 'previous'
        self.staging_dir = self.root / 'staging'
        self.state_file = self.root / 'installed.json'
        self.pending_file = self.root / 'pending.json'
        self.results_file = self.root / 'results.json'
        self.lock_file = self.root / 'apply.lock'
        self.booted_file = self.root / 'booted.json'
        self.backups_dir = self.data_dir / 'backups'
        self.db_file = self.data_dir / 'db.sqlite3'

    # State
    def state(self):
        return _read_json(self.state_file, {'modules': {}})

    def save_state(self, state):
        _write_json(self.state_file, state)

    def installed(self):
        """{label: entry} of installed modules; an entry is the manifest plus
        ``enabled``, ``installed_at`` and ``previous`` (for rolling back)."""
        return self.state()['modules']

    def enabled_labels(self):
        mods = {l: e for l, e in self.installed().items() if e.get('enabled', True)}
        try:
            return dependency_order(mods)
        except PackageError:
            return sorted(mods)

    # Queue
    def pending(self):
        return _read_json(self.pending_file, None)

    def queue(self, ops, user='', demo=()):
        """Queue ``ops`` to be applied at the next start. Raises if a change is already waiting."""
        if self.pending_file.exists():
            raise PackageError('Another change is already waiting to be applied. Wait for the restart to finish.')
        job = {'id': uuid.uuid4().hex[:12], 'requested_by': user, 'requested_at': _now(), 'ops': ops, 'demo': list(demo)}
        _write_json(self.pending_file, job)
        return job

    def cancel_pending(self):
        job = self.pending()
        if job:
            for op in job['ops']:
                if op.get('staging'):
                    _rmtree(self.root / op['staging'])
            self.pending_file.unlink(missing_ok=True)

    def new_staging(self):
        path = self.staging_dir / uuid.uuid4().hex[:12]
        path.mkdir(parents=True)
        return path

    def results(self):
        return _read_json(self.results_file, [])

    def _add_result(self, result):
        _write_json(self.results_file, ([result] + self.results())[:KEEP_RESULTS])

    # Backups
    def backup_db(self, reason):
        """Copy the database to ``backups/`` (safe while it's in use). Returns the file name."""
        if not self.db_file.exists():
            return ''
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r'[^a-z0-9]+', '-', reason.lower()).strip('-')[:40]
        name = f'db-{datetime.now():%Y%m%d-%H%M%S}-{slug}.sqlite3'
        _copy_db(self.db_file, self.backups_dir / name)
        self._prune_backups()
        return name

    def backups(self):
        if not self.backups_dir.is_dir():
            return []
        return sorted(self.backups_dir.glob('db-*.sqlite3'), key=lambda p: p.name, reverse=True)

    def _prune_backups(self):
        keep = {p['backup'] for e in self.installed().values() for p in e.get('previous', []) if p.get('backup')}
        for old in self.backups()[KEEP_BACKUPS:]:
            if old.name not in keep:
                old.unlink(missing_ok=True)

    def restore_db(self, name):
        src = self.backups_dir / name
        if not src.is_file():
            raise PackageError(f'The backup {name} is missing.')
        self.replace_db(src)

    def replace_db(self, src):
        for suffix in ('-wal', '-shm', '-journal'):
            Path(str(self.db_file) + suffix).unlink(missing_ok=True)
        shutil.copyfile(src, self.db_file)


def _copy_db(src, dst):
    """Copy an SQLite database, safely even while it's in use."""
    a, b = sqlite3.connect(src), sqlite3.connect(dst)
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()


# -- Moving all the data to another Osmia ------------------------------------------

def _export_files(folder):
    """The files of ``folder`` that go into an export (caches left out)."""
    folder = Path(folder)
    if not folder.is_dir():
        return
    for path in sorted(folder.rglob('*')):
        rel = path.relative_to(folder).as_posix()
        if path.is_file() and not any(s in rel + '/' for s in SKIPPED) and not rel.endswith('.pyc'):
            yield path, rel


def export_sizes(store, media_root, modules, module_dir):
    """What an export would hold, in bytes before compression:
    {'database', 'files', 'file_count', 'modules', 'total'}."""
    database = sum(p.stat().st_size for p in (store.db_file, Path(str(store.db_file) + '-wal')) if p.exists())
    media = [p.stat().st_size for p, _ in _export_files(media_root)]
    code = sum(p.stat().st_size for label in modules for p, _ in _export_files(Path(module_dir) / label))
    return {'database': database, 'files': sum(media), 'file_count': len(media), 'modules': code,
            'total': database + sum(media) + code}


def export_data(store, out, media_root, modules, module_dir, user=''):
    """Write everything this Osmia holds to the zip file ``out`` (a path or a file):

        osmia-data.json     what's in it: Osmia version, modules, when and by whom
        db.sqlite3          the database
        media/...           the uploaded images and files
        modules/<label>/... the installed modules, so the other Osmia runs the same code

    ``modules`` is {label: installed entry}; their folders are in ``module_dir``.
    Returns the info written to osmia-data.json.
    """
    media_root, module_dir = Path(media_root), Path(module_dir)
    info = {
        'format': DATA_FORMAT, 'osmia': OSMIA_VERSION, 'exported_at': _now(), 'exported_by': user,
        'modules': {l: {k: v for k, v in e.items() if k not in ('previous', 'installed_at')} for l, e in modules.items()},
        'files': 0,
    }
    tmp = Path(tempfile.mkdtemp())
    try:
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            if store.db_file.exists():
                _copy_db(store.db_file, tmp / 'db.sqlite3')  # a consistent copy, even with people using Osmia
                zf.write(tmp / 'db.sqlite3', 'db.sqlite3')
            folders = [(media_root, 'media')] + [(module_dir / l, f'modules/{l}') for l in modules]
            for folder, prefix in folders:
                for path, rel in _export_files(folder):
                    stored = path.suffix.lower() in STORED_AS_IS
                    zf.write(path, f'{prefix}/{rel}', compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
                    info['files'] += prefix == 'media'
            zf.writestr(DATA_INFO, json.dumps(info, indent=2, ensure_ascii=False))
    finally:
        _rmtree(tmp)
    return info


def _data_members(zf):
    """The zip's files, checked: no unsafe paths or links, not too many, not too big."""
    members = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace('\\', '/')
        parts = name.split('/')
        if name.startswith('/') or '..' in parts or ':' in parts[0]:
            raise PackageError(f'The zip contains an unsafe path: {info.filename}')
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise PackageError(f'The zip contains a link, which isn\'t allowed: {info.filename}')
        if name != DATA_INFO and name != 'db.sqlite3' and parts[0] not in ('media', 'modules'):
            raise PackageError(f'The zip contains something that isn\'t Osmia data: {info.filename}')
        members.append((name, info))
    if len(members) > MAX_DATA_FILES:
        raise PackageError(f'The zip holds more than {MAX_DATA_FILES} files.')
    if sum(i.file_size for _, i in members) > MAX_DATA_BYTES:
        raise PackageError(f'The data is larger than {MAX_DATA_BYTES // 1024 ** 3} GB unpacked.')
    return members


def read_export(data):
    """Check an Osmia data export (a path or a file) and return its osmia-data.json."""
    try:
        zf = zipfile.ZipFile(data)
    except zipfile.BadZipFile:
        raise PackageError('That is not a .zip file.')
    with zf:
        if DATA_INFO not in zf.namelist():
            raise PackageError(f'This is not an Osmia data export (it has no {DATA_INFO}). '
                               'Make one with "Export all data" on the Modules page of the other Osmia.')
        names = {n for n, _ in _data_members(zf)}
        try:
            info = json.loads(zf.read(DATA_INFO).decode('utf-8'))
        except ValueError as exc:
            raise PackageError(f'{DATA_INFO} is not valid JSON: {exc}')
        if not isinstance(info, dict) or info.get('format') != DATA_FORMAT or not isinstance(info.get('modules'), dict):
            raise PackageError('This data export was made by a version of Osmia that this one can\'t read.')
        if parse_version(info.get('osmia')) > parse_version(OSMIA_VERSION):
            raise PackageError(f'The data comes from Osmia {info["osmia"]}; this is the older Osmia {OSMIA_VERSION}. '
                               'Update this Osmia first.')
        if 'db.sqlite3' not in names:
            raise PackageError('The export has no database (db.sqlite3).')
        for label, entry in info['modules'].items():
            if f'modules/{label}/manifest.json' not in names:
                raise PackageError(f'The export lists the module "{label}" but doesn\'t contain it.')
            try:
                manifest = check_manifest(json.loads(zf.read(f'modules/{label}/manifest.json').decode('utf-8-sig')), label)
            except ValueError as exc:
                raise PackageError(f'manifest.json of {label} is not valid JSON: {exc}')
            entry.update(manifest)
        problems = _broken_dependencies(info['modules'])
        if problems:
            raise PackageError(' '.join(problems))
        return info


def extract_export(data, dest):
    """Unpack a checked data export into the folder ``dest``."""
    dest = Path(dest)
    total = 0
    with zipfile.ZipFile(data) as zf:
        for name, member in _data_members(zf):
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, 'wb') as out:
                while chunk := src.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_DATA_BYTES:  # sizes in a zip can lie
                        raise PackageError('The data is larger than allowed unpacked.')
                    out.write(chunk)


# -- Applying the queue (before Django starts) ------------------------------------

class _Journal:
    """Undo steps for file and state changes, replayed backwards on failure."""

    def __init__(self, store):
        self.store, self.steps = store, []

    def move(self, src, dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            _rmtree(dst)
        shutil.move(str(src), str(dst))
        self.steps.append(lambda: (dst.exists() and shutil.move(str(dst), str(src))))

    def undo(self):
        for step in reversed(self.steps):
            try:
                step()
            except OSError:
                pass


def _manage(args, log, env=None):
    """Run ``manage.py <args>`` in a fresh process; True if it worked."""
    cmd = [sys.executable, str(BASE_DIR / 'manage.py'), *args]
    log.append('$ manage.py ' + ' '.join(args))
    proc = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, encoding='utf-8', errors='replace',
                          env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'OSMIA_SAFE_MODE': '', **(env or {})})
    out = (proc.stdout + proc.stderr).strip()
    if out:
        log.append(out[-6000:])
    return proc.returncode == 0


def _describe(op, installed):
    label = op['label']
    title = op.get('title') or installed.get(label, {}).get('title') or label
    return {
        'install': lambda: (f'Upgrade {title} {op.get("from_version")} → {op["version"]}' if op.get('from_version')
                            else f'Install {title} {op["version"]}'),
        'enable': lambda: f'Enable {title}',
        'disable': lambda: f'Disable {title}',
        'uninstall': lambda: f'Uninstall {title}' + (' and delete its data' if op.get('delete_data') else ''),
        'rollback': lambda: f'Roll back {title}',
        'import': lambda: f'Replace all data with {op.get("source") or "an export"}',
    }[op['op']]()


def _apply_import(store, op, mods, journal, log, stamp):
    """Swap in an unpacked data export: its database, uploaded files and modules.
    What was here is kept (previous/before-import-*, backups/media-before-import-*)."""
    src = store.root / op['staging']
    info = _read_json(src / DATA_INFO, None)
    if not info:
        raise PackageError('The unpacked export is missing.')
    if store.modules_dir.exists():
        journal.move(store.modules_dir, store.previous_dir / f'before-import-{stamp}')
    if (src / 'modules').exists():
        journal.move(src / 'modules', store.modules_dir)
    mods.clear()
    mods.update({label: {**entry, 'enabled': entry.get('enabled', True), 'installed_at': _now(), 'previous': []}
                 for label, entry in info['modules'].items()})
    log.append('Modules: ' + (', '.join(f'{e["title"]} {e["version"]}' for e in mods.values()) or 'none'))
    media = Path(op['media_root'])
    if media.exists():
        kept = store.backups_dir / f'media-before-import-{stamp}'
        journal.move(media, kept)
        log.append(f'The uploaded files that were here are kept in backups/{kept.name}')
    if (src / 'media').exists():
        journal.move(src / 'media', media)
    log.append(f'Uploaded files: {info.get("files", 0)}')
    store.replace_db(src / 'db.sqlite3')
    log.append(f'Database replaced with the one exported from Osmia {info.get("osmia")} '
               f'at {info.get("exported_at", "?")} by {info.get("exported_by") or "?"}')


def apply_pending(store):
    """Apply the queued change, or undo all of it. Returns the result (also saved)."""
    job = store.pending()
    if not job:
        return None
    state = store.state()
    before_state = json.loads(json.dumps(state))
    mods = state['modules']
    log, journal = [], _Journal(store)
    started = time.time()
    descriptions = [_describe(op, mods) for op in job['ops']]
    log.append('Requested by ' + (job.get('requested_by') or 'unknown') + ' at ' + job.get('requested_at', ''))
    backup = ''
    ok = True
    try:
        backup = store.backup_db('before ' + ', '.join(descriptions))
        if backup:
            log.append(f'Database backed up to backups/{backup}')
        for op, desc in zip(job['ops'], descriptions):
            log.append('— ' + desc)
            if op['op'] == 'import':
                _apply_import(store, op, mods, journal, log, f'{datetime.now():%Y%m%d-%H%M%S}')
                continue
            label = op['label']
            current = store.modules_dir / label
            if op['op'] == 'install':
                src = store.root / op['staging'] / label
                manifest = read_manifest(src)
                entry = mods.get(label, {})
                previous = entry.get('previous', [])
                if current.exists():
                    old_version = entry.get('version', 'unknown')
                    keep = store.previous_dir / label / f'{old_version}-{int(started)}'
                    journal.move(current, keep)
                    previous = [{'version': old_version, 'path': keep.relative_to(store.root).as_posix(),
                                 'backup': backup, 'replaced_at': _now()}] + previous[:2]
                journal.move(src, current)
                mods[label] = {**manifest, 'enabled': True, 'installed_at': _now(), 'previous': previous}
            elif op['op'] in ('enable', 'disable'):
                mods[label]['enabled'] = op['op'] == 'enable'
            elif op['op'] == 'uninstall':
                if op.get('delete_data'):
                    # Unapply its migrations while it's still installed (and on), so its tables go.
                    mods[label]['enabled'] = True
                    store.save_state(state)
                    if not _manage(['migrate', label, 'zero', '--noinput'], log):
                        raise PackageError(f'Could not delete the data of {label}.')
                if current.exists():
                    journal.move(current, store.previous_dir / label / f'uninstalled-{int(started)}')
                mods.pop(label, None)
            elif op['op'] == 'rollback':
                prev = mods[label]['previous'][0]
                if prev.get('backup'):
                    store.restore_db(prev['backup'])
                    log.append(f'Database restored from backups/{prev["backup"]}')
                journal.move(current, store.previous_dir / label / f'rolled-back-{mods[label]["version"]}-{int(started)}')
                journal.move(store.root / prev['path'], current)
                mods[label] = {**read_manifest(current), 'enabled': True, 'installed_at': _now(),
                               'previous': mods[label]['previous'][1:]}
        problems = _broken_dependencies(mods)
        if problems:
            raise PackageError(' '.join(problems))
        store.save_state(state)
        if not _manage(['migrate', '--noinput'], log):
            raise PackageError('Updating the database failed.')
        if not _manage(['check'], log):
            raise PackageError('Osmia did not start with this change.')
        demo = [l for l in job.get('demo', []) if l in mods or l in BUILTIN]
        if demo and not _manage(['load_demo', '--modules', ','.join(demo)], log):
            log.append('Loading demo data failed; the modules are installed without it.')
    except Exception as exc:  # anything at all: put everything back
        ok = False
        log.append(f'FAILED: {exc}')
        journal.undo()
        store.save_state(before_state)
        if backup:
            try:
                store.restore_db(backup)
                log.append('Database put back from the backup.')
            except Exception as restore_exc:
                log.append(f'Could not put the database back: {restore_exc}. The backup is backups/{backup}.')
        log.append('Everything was put back as it was before.')
    for op in job['ops']:
        if op.get('staging'):
            _rmtree(store.root / op['staging'])
    result = {'id': job['id'], 'ok': ok, 'changes': descriptions, 'requested_by': job.get('requested_by', ''),
              'finished_at': _now(), 'seconds': round(time.time() - started, 1), 'backup': backup, 'log': '\n'.join(log)}
    store._add_result(result)
    store.pending_file.unlink(missing_ok=True)
    return result


def _broken_dependencies(mods):
    enabled = {l for l, e in mods.items() if e.get('enabled', True)} | set(BUILTIN)
    return [f'{e["title"]} needs {dep}, which would not be installed and enabled.'
            for l, e in mods.items() if e.get('enabled', True) for dep in e.get('depends', []) if dep not in enabled]


def _print_safely(text):
    """Print to the console even when it can't show every character (e.g. a cp932 Windows console)."""
    enc = getattr(sys.stderr, 'encoding', None) or 'utf-8'
    print(text.encode(enc, 'replace').decode(enc, 'replace'), file=sys.stderr, flush=True)


def _migrations_fingerprint(store):
    """A short hash of every migration file Osmia would run, so new migrations
    (e.g. after a git pull) are noticed and applied at the next start."""
    import hashlib
    labels = store.enabled_labels()
    if os.environ.get('OSMIA_DEV_MODULES'):
        folders = [BUNDLED_DIR / l for l in bundled_manifests()]
    else:
        folders = [store.modules_dir / l for l in labels]
    names = []
    for folder in [BASE_DIR / 'core', BASE_DIR / 'users'] + folders:
        mig = folder / 'migrations'
        if mig.is_dir():
            names += sorted(f'{folder.name}/{p.name}' for p in mig.glob('[0-9]*.py'))
    return hashlib.sha1('\n'.join(names).encode()).hexdigest()[:16]


def boot(store):
    """Called once per start, before Django loads: apply a queued change and
    bring the database up to date. Safe to call from several processes."""
    store.root.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(store.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - store.lock_file.stat().st_mtime > 900:  # left over from a crash
            store.lock_file.unlink(missing_ok=True)
            return boot(store)
        while store.lock_file.exists():  # another process is applying it
            time.sleep(0.5)
        return None
    os.close(fd)
    try:
        result = apply_pending(store)
        marker = {'osmia': OSMIA_VERSION, 'modules': store.enabled_labels(), 'migrations': _migrations_fingerprint(store)}
        if result is None and (not store.db_file.exists() or _read_json(store.booted_file, None) != marker):
            # First start, a new Osmia version, new migrations or modules changed by hand: migrate.
            log = []
            if not _manage(['migrate', '--noinput'], log):
                _print_safely('\n'.join(log))
        _write_json(store.booted_file, marker)
        return result
    finally:
        store.lock_file.unlink(missing_ok=True)
