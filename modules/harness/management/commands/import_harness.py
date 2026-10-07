"""Import projects and signal rules from the stand-alone Wire Harness Designer.

    python manage.py import_harness_devices "C:/.../python code/harness"   # Devices module, first
    python manage.py import_harness "C:/.../python code/harness"

Devices are created only by the Devices module, so this command never creates
one: each project's devices are matched to devices already in Devices (by name
and part number, as ``import_harness_devices`` created them). Every project
comes in with all its versions; the signal rules are merged in.
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from devices.management.commands.import_harness_devices import current_definition, find_device, read_json, versions_of
from harness.models import HarnessProject, HarnessProjectVersion, SignalRule


class Command(BaseCommand):
    help = 'Import projects and signal rules from a Wire Harness Designer folder (devices must be imported first).'

    def add_arguments(self, parser):
        parser.add_argument('folder')

    @transaction.atomic
    def handle(self, folder, **options):
        root = Path(folder)
        if not (root / 'devices').is_dir():
            raise CommandError(f'{root} has no devices/ folder; is this the harness app?')

        device_ids, missing = {}, []
        for item_dir in sorted(p for p in (root / 'devices').iterdir() if p.is_dir()):
            data = current_definition(item_dir)
            device = find_device(data) if data else None
            if device:
                device_ids[item_dir.name] = str(device.pk)
            elif data:
                missing.append(data.get('name') or item_dir.name)
        if missing:
            raise CommandError(
                f'These devices are not in Devices yet: {", ".join(missing)}. '
                f'Run "manage.py import_harness_devices {folder}" first.'
            )

        projects_dir = root / 'projects'
        for item_dir in sorted(p for p in projects_dir.iterdir() if p.is_dir()) if projects_dir.is_dir() else []:
            versions, current = versions_of(item_dir)
            if not versions:
                continue
            project = HarnessProject.objects.create(name=versions[-1][1].get('name') or item_dir.name)
            for number, data in versions:
                for inst in data.get('instances', []):
                    inst['device_id'] = device_ids.get(inst.get('device_id'), inst.get('device_id'))
                HarnessProjectVersion.objects.create(project=project, version=number, data=data)
            project.version = current
            project.name = project.data(current).get('name') or project.name
            project.save()
            self.stdout.write(f'  project {project.name}: {len(versions)} version(s), now v{current}')

        rules = root / 'settings' / 'signal_rules.json'
        if rules.exists():
            pairs = read_json(rules).get('pairs', [])
            SignalRule.replace_all(SignalRule.pairs() + pairs)
            self.stdout.write(f'  signal rules: {len(pairs)} imported')
        self.stdout.write(self.style.SUCCESS('Harness projects imported.'))
