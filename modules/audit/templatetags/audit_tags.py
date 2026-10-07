from django import template
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils.html import format_html

from core.modules import get_module

register = template.Library()


@register.filter
def module_title(label):
    module = get_module(label)
    return f'{module.manifest.icon} {module.manifest.title}' if module else (label or 'System')


@register.simple_tag
def history_url(record):
    """Link target for a record's full change history."""
    ct = ContentType.objects.get_for_model(record)
    return reverse('audit:history', args=[ct.pk, record.pk])


@register.inclusion_tag('audit/_chain_tag.html')
def chain_tag(entry):
    return {'e': entry}
