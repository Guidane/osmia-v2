"""
WSGI config for osmia project.

It exposes the WSGI callable as a module-level variable named ``application``.
Run it with ``serve.py``, which restarts it when modules change.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

from osmia import addons, settings_paths

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'osmia.settings')

# Apply module changes queued from the website, before Django loads the modules.
if not os.environ.get('OSMIA_DEV_MODULES'):
    addons.boot(addons.Store(settings_paths.data_dir()))

application = get_wsgi_application()
