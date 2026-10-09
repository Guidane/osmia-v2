"""Attaching images and other files to records: checking, resizing and storing uploads."""
import io
import os
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
IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'jpe', 'png', 'gif', 'webp', 'bmp', 'tif', 'tiff'}
# Files other than pictures, stored as they are. PDFs open in the browser, the
# rest are downloaded (never shown inline, so an uploaded page can't run scripts).
FILE_EXTENSIONS = {
    'pdf', 'txt', 'csv', 'rtf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt', 'ods', 'odp',
    'zip', '7z', 'step', 'stp', 'iges', 'igs', 'stl', 'dxf', 'dwg',
}
ACCEPT = 'image/*,' + ','.join(f'.{ext}' for ext in sorted(FILE_EXTENSIONS))  # for <input type=file>


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


def _next_position(record):
    last = record.images.aggregate(m=Max('position'))['m']
    return 0 if last is None else last + 1


def _add_file(record, upload, name, ext, user, caption):
    """Store a non-picture upload as it is."""
    if ext == 'pdf':
        head = upload.read(1024)
        upload.seek(0)
        if b'%PDF-' not in head:
            raise ValidationError(f'{name} is not a PDF Osmia can read.')
    item = Image(record=record, caption=caption[:200], name=name[:255], uploaded_by=user if getattr(user, 'pk', None) else None,
                 position=_next_position(record))
    item.file.save(f'{uuid.uuid4().hex}.{ext}', upload, save=False)
    item.save()
    return item


def add_image(record, upload, user=None, caption=''):
    """Store an uploaded file as an image of ``record``. A picture is turned
    upright, scaled down to at most MAX_SIDE and re-encoded, which also drops
    its metadata (camera, GPS position, ...). Other files (FILE_EXTENSIONS,
    e.g. a PDF) are kept as they are."""
    name = os.path.basename(getattr(upload, 'name', '') or 'image')
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError(f'{name} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.')
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if ext in FILE_EXTENSIONS:
        return _add_file(record, upload, name, ext, user, caption)
    if ext and ext not in IMAGE_EXTENSIONS:
        raise ValidationError(f'{name}: .{ext} files can\'t be added. Use an image, a PDF or another document '
                              f'({", ".join(sorted(FILE_EXTENSIONS))}).')
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
    image = Image(record=record, caption=caption[:200], name=name[:255], uploaded_by=user if getattr(user, 'pk', None) else None,
                  position=_next_position(record), width=width, height=height)
    image.file.save(f'{stem}.{ext}', ContentFile(full), save=False)
    image.thumb.save(f'{stem}.{thumb_ext}', ContentFile(small), save=False)
    image.save()
    return image


def make_cover(image):
    first = Image.objects.filter(content_type=image.content_type, object_id=image.object_id).order_by('position').first()
    if first and first.pk != image.pk:
        image.position = first.position - 1
        image.save(update_fields=['position'])
