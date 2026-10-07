"""Attaching images to records: checking, resizing and storing uploads."""
import io
import uuid

from django.contrib.contenttypes.fields import GenericRelation
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db.models import Max
from PIL import Image as PILImage
from PIL import ImageOps, UnidentifiedImageError

from .models import Image

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_FILES = 20
MAX_SIDE = 2000   # stored image, longest side
THUMB_SIDE = 400  # thumbnail, longest side
FORMATS = {'JPEG', 'PNG', 'GIF', 'WEBP', 'BMP', 'TIFF'}


def accepts_images(model):
    """Does ``model`` have an ``images`` GenericRelation to core.Image?"""
    field = next((f for f in model._meta.private_fields if f.name == 'images'), None)
    return isinstance(field, GenericRelation) and field.related_model is Image


def can_edit_images(user, record):
    """Anyone logged in, unless the model says otherwise with
    ``images_editable_by(user)`` (e.g. only you and managers change your photos)."""
    if not user.is_authenticated:
        return False
    check = getattr(record, 'images_editable_by', None)
    return check(user) if check else True


def _encode(img, side):
    """(bytes, extension, (width, height)) of ``img`` scaled to fit ``side``."""
    img = img.copy()
    img.thumbnail((side, side))
    transparent = img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info)
    out = io.BytesIO()
    if transparent:
        img.convert('RGBA').save(out, 'PNG', optimize=True)
        return out.getvalue(), 'png', img.size
    img.convert('RGB').save(out, 'JPEG', quality=85, optimize=True)
    return out.getvalue(), 'jpg', img.size


def add_image(record, upload, user=None, caption=''):
    """Store an uploaded file as an image of ``record``. The picture is turned
    upright, scaled down to at most MAX_SIDE and re-encoded, which also drops
    its metadata (camera, GPS position, ...)."""
    name = getattr(upload, 'name', 'image')
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError(f'{name} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.')
    try:
        img = PILImage.open(upload)
        if img.format not in FORMATS:
            raise ValidationError(f'{name} is not a supported image (use JPEG, PNG, GIF, WebP, BMP or TIFF).')
        img.load()
        img = ImageOps.exif_transpose(img)
    except (UnidentifiedImageError, OSError, PILImage.DecompressionBombError, ValueError):
        raise ValidationError(f'{name} is not an image Osmia can read.')
    stem = uuid.uuid4().hex
    full, ext, (width, height) = _encode(img, MAX_SIDE)
    small, thumb_ext, _ = _encode(img, THUMB_SIDE)
    last = record.images.aggregate(m=Max('position'))['m']
    image = Image(record=record, caption=caption[:200], uploaded_by=user if getattr(user, 'pk', None) else None,
                  position=0 if last is None else last + 1, width=width, height=height)
    image.file.save(f'{stem}.{ext}', ContentFile(full), save=False)
    image.thumb.save(f'{stem}.{thumb_ext}', ContentFile(small), save=False)
    image.save()
    return image


def make_cover(image):
    first = Image.objects.filter(content_type=image.content_type, object_id=image.object_id).order_by('position').first()
    if first and first.pk != image.pk:
        image.position = first.position - 1
        image.save(update_fields=['position'])
