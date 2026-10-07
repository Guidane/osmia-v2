from django.apps import AppConfig
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import autodiscover_modules


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        # Fail fast on a broken module set: a missing dependency would otherwise
        # surface later as a confusing import error.
        errors = check_module_dependencies(None)
        if errors:
            raise ImproperlyConfigured('\n'.join(f'{e.msg} {e.hint or ""}' for e in errors))
        checks.register(check_module_dependencies)
        # Load every installed app's hooks.py so modules can extend each other.
        autodiscover_modules('hooks')


def check_module_dependencies(app_configs, **kwargs):
    from .modules import dependency_order, installed_modules

    errors = []
    labels = {c.label for c in installed_modules()}
    for config in installed_modules():
        for dep in config.manifest.depends:
            if dep not in labels:
                errors.append(checks.Error(
                    f"Module '{config.label}' depends on '{dep}', which is not installed.",
                    hint=f"Install '{dep}' on the Modules page, or disable '{config.label}'.",
                    id='osmia.E001',
                ))
    try:
        dependency_order()
    except RuntimeError as exc:
        errors.append(checks.Error(str(exc), id='osmia.E002'))
    return errors
