"""{% image_gallery record %} shows a record's images (and lets people add them);
{% cover_thumb record %} shows its first image small, e.g. in a list."""
from django import template
from django.utils.html import format_html

from core.images import can_edit_images

register = template.Library()


@register.inclusion_tag('core/_gallery.html', takes_context=True)
def image_gallery(context, record, title='Images', empty=''):
    request = context['request']
    return {
        'record': record,
        'images': list(record.images.all()),
        'title': title,
        'empty': empty,
        'can_edit': can_edit_images(request.user, record),
        'model_label': record._meta.label_lower,
        'next': request.get_full_path(),
        'csrf_token': context.get('csrf_token'),
    }


@register.simple_tag
def cover_thumb(record, size=40, css_class='thumb'):
    """The record's cover image as a small square (nothing if it has none).
    Prefetch ``images`` when showing many records."""
    cover = next(iter(record.images.all()), None)
    if cover is None:
        return ''
    return format_html('<img class="{}" src="{}" alt="" loading="lazy" width="{}" height="{}">', css_class, cover.thumb_url, size, size)
