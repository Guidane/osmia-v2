from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.urls import reverse

from core import automation, hooks
from core.automation import Param

from .models import Notification, Rule

# -- The "Notify people" action -----------------------------------------------------


@automation.action('automations.notify', 'Notify people', params=[
    Param('users', 'People', 'users'),
    Param('owner', "Also the record's owner", 'bool',
          help='Its assignee, responsible person or creator.'),
    Param('message', 'Message', 'textarea', required=True,
          help='Use {object} or {source} to name the record, e.g. "{source} has arrived".'),
    Param('link', 'Link to', 'target'),
])
def notify(ctx, params):
    from tasks.hooks import _owner

    people = list(get_user_model().objects.filter(pk__in=[p for p in params.get('users') or [] if str(p).isdigit()], is_active=True))
    if params.get('owner'):
        owner = _owner(ctx.get('object'))
        if owner is not None and owner not in people:
            people.append(owner)
    if not people:
        raise ValidationError('Nobody to notify: pick people, or the record has no owner.')
    message = automation.render(params.get('message'), ctx).strip()[:500]
    if not message:
        raise ValidationError('The notification has no message.')
    target = ctx.get(params.get('link') or 'object')
    url = target.get_absolute_url() if hasattr(target, 'get_absolute_url') else ''
    Notification.objects.bulk_create([Notification(user=p, message=message, url=url, rule=ctx.get('rule')) for p in people])
    return f'Notified {", ".join(p.get_full_name() or p.username for p in people)}: {message}'


# -- The bell in the top bar ------------------------------------------------------------

@hooks.register('topbar_items')
def bell(request):
    unread = Notification.objects.filter(user=request.user, read=False).count()
    return render_to_string('automations/_bell.html', {'unread': unread, 'url': reverse('automations:notifications')})


@hooks.register('dashboard_widgets')
def active_rules(request):
    return hooks.Widget('Active automation rules', Rule.objects.filter(active=True).count(), reverse('automations:list'))
