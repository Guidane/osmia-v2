"""Import the device library of the stand-alone Wire Harness Designer.

    python manage.py import_harness_devices "C:/.../python code/harness"

Every device in its devices/ folder becomes an Osmia device, with all its
versions (the current one active). Harness "users" are matched to Osmia users
by username or full name; unmatched owners are left blank. A device that
already exists (same name and part number) is skipped, so it's safe to run
again. Run this before ``import_harness``, which brings in the projects.
"""
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from devices.models import Device, DeviceVersion


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def versions_of(item_dir):
    """[(version, data), ...] ascending, and the current version number."""
    numbers = sorted(int(p.stem) for p in item_dir.glob('*.json') if p.stem.isdigit())
    pointer = item_dir / 'pointer.json'
    current = read_json(pointer).get('current') if pointer.exists() else (numbers[-1] if numbers else None)
    return [(n, read_json(item_dir / f'{n}.json')) for n in numbers], current


def current_definition(item_dir):
    """The data of the current version of one stand-alone device folder."""
    versions, current = versions_of(item_dir)
    return dict(versions).get(current) if versions else None


def find_device(data):
    """The Osmia device a stand-alone device definition was imported as."""
    return Device.objects.filter(name=(data.get('name') or '').strip(),
                                 part_number=(data.get('part_number') or '').strip()).first()


class Command(BaseCommand):
    help = "Import the stand-alone Wire Harness Designer's devices (with versions) into Devices."

    def add_arguments(self, parser):
        parser.add_argument('folder')

    @transaction.atomic
    def handle(self, folder, **options):
        root = Path(folder)
        if not (root / 'devices').is_dir():
            raise CommandError(f'{root} has no devices/ folder; is this the harness app?')
        users = self.match_users(root)
        for item_dir in sorted(p for p in (root / 'devices').iterdir() if p.is_dir()):
            versions, current = versions_of(item_dir)
            if not versions:
                continue
            existing = find_device(dict(versions)[current])
            if existing:
                self.stdout.write(f'  device {existing.name}: already in Devices, skipped')
                continue
            device = Device.objects.create(name=versions[-1][1].get('name') or item_dir.name)
            for number, data in versions:
                device.apply_definition({**data, 'responsible_user_id': users.get(data.get('responsible_user_id'))})
                DeviceVersion.objects.create(device=device, version=number, data=device.definition())
            device.activate_version(current)
            self.stdout.write(f'  device {device.name}: {len(versions)} version(s), now v{current}')
        self.stdout.write(self.style.SUCCESS('Devices imported.'))

    def match_users(self, root):
        """{harness user id: Osmia user pk as str}, matched by name."""
        path = root / 'settings' / 'users.json'
        if not path.exists():
            return {}
        by_name = {}
        for u in get_user_model().objects.all():
            by_name[u.username.lower()] = u
            by_name[str(u).lower()] = u
        matched = {}
        for u in read_json(path).get('users', []):
            user = by_name.get((u.get('name') or '').strip().lower())
            if user:
                matched[u['id']] = str(user.pk)
            else:
                self.stdout.write(f'  no Osmia user named "{u.get("name")}"; their devices get no owner')
        return matched
