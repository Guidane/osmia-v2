#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'osmia.settings')
    if (sys.argv[1:2] == ['runserver'] and not os.environ.get('OSMIA_DEV_MODULES')
            and (os.environ.get('RUN_MAIN') == 'true' or '--noreload' in sys.argv)):
        # The process that serves (not the autoreloader watching it): apply
        # module changes queued from the website before Django loads them.
        from osmia import addons, settings_paths
        addons.boot(addons.Store(settings_paths.data_dir()))
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
