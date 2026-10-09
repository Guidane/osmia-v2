"""{% image_gallery record %} shows a record's images and files (and lets people add them);
{% cover_thumb record %} shows its first image small, e.g. in a list."""
from django import template
from django.utils.html import format_html

from core.images import ACCEPT, can_edit_images

register = template.Library()


@register.inclusion_tag('core/_gallery.html', takes_context=True)
def image_gallery(context, record, title='Images & files', empty=''):
    request = context['request']
    images = list(record.images.all())
    cover = next((i for i in images if i.is_image), None)
    return {
        'record': record,
        'images': images,
        'cover_id': cover.pk if cover and len(images) > 1 else None,
        'accept': ACCEPT,
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
    cover = next((i for i in record.images.all() if i.is_image), None)
    if cover is None:
        return ''
    return format_html('<img class="{}" src="{}" alt="" loading="lazy" width="{}" height="{}">', css_class, cover.thumb_url, size, size)
