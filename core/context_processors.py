from django.conf import settings

from . import hooks
from .modules import get_module, installed_modules


def osmia(request):
    match = getattr(request, 'resolver_match', None)
    current = get_module(match.namespace) if match and match.namespace else None
    log_url = ''
    if current is not None:
        log_url = next(iter(hooks.collect('module_log_url', request, current)), '')
    return {
        'osmia_modules': installed_modules(),
        'current_module': current,
        'module_log_url': log_url,  # the Audit module's log of this module
        # e.g. the notifications bell from Automations
        'topbar_items': hooks.collect('topbar_items', request) if request.user.is_authenticated else [],
        'osmia_safe_mode': settings.OSMIA_SAFE_MODE,  # started without modules (serve.py, after a failed start)
    }
