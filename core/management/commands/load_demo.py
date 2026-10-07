from importlib import import_module
from importlib.util import find_spec

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core import audit
from core.modules import dependency_order


def load_demo(labels=None, log=lambda line: None):
    """Load the demo data of the given modules (all if None), in dependency order.
    Each module's ``demo.load()`` only adds what's missing, so running it twice is fine."""
    loaded = []
    with transaction.atomic():
        for config in dependency_order():
            if labels is not None and config.label not in labels:
                continue
            if find_spec(f"{config.name}.demo") is None:
                continue
            with audit.acting(config.label, source='demo data'):
                import_module(f"{config.name}.demo").load()
            loaded.append(config.label)
            log(f"  loaded demo data for {config.label}")
    return loaded


class Command(BaseCommand):
    help = "Load demo data from every installed module's demo.py, in dependency order."

    def add_arguments(self, parser):
        parser.add_argument('--modules', help='Only these modules, e.g. "departments,tasks".')

    def handle(self, *args, modules=None, **options):
        labels = {m.strip() for m in modules.split(',') if m.strip()} if modules else None
        unknown = labels - {c.label for c in dependency_order()} if labels else set()
        if unknown:
            raise CommandError(f"Not installed: {', '.join(sorted(unknown))}")
        load_demo(labels, self.stdout.write)
        self.stdout.write(self.style.SUCCESS("Demo data loaded."))
