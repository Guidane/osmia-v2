"""``{% versioned_static 'path' %}``: a static URL that changes whenever the file does.

Browsers cache CSS and JS; adding the file's modification time as ``?v=``
makes them fetch the new copy after an edit, without a hard refresh.
"""
import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def versioned_static(path):
    url = static(path)
    found = finders.find(path)
    return f'{url}?v={int(os.path.getmtime(found))}' if found else url
