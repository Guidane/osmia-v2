from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def home(request):
    """The landing page: just the installed modules."""
    return render(request, 'core/home.html')


# -- Images attached to records ----------------------------------------------------

from django.apps import apps
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import images as image_store
from .models import Image


def _back(request, record):
    target = request.POST.get('next') or ''
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(target)
    return redirect(record.get_absolute_url() if record is not None and hasattr(record, 'get_absolute_url') else 'core:home')


def _editable(request, image):
    record = image.record
    if record is None or not image_store.can_edit_images(request.user, record):
        raise PermissionDenied
    return record


@login_required
def image_file(request, pk):
    image = get_object_or_404(Image, pk=pk)
    f = image.thumb if request.GET.get('thumb') and image.thumb else image.file
    try:
        response = FileResponse(f.open('rb'))
    except FileNotFoundError:
        raise Http404('The image file is missing.')
    response['Cache-Control'] = 'private, max-age=86400'
    return response


@login_required
@require_POST
def image_upload(request, model, pk):
    try:
        model = apps.get_model(model)
    except (LookupError, ValueError):
        raise Http404
    if not image_store.accepts_images(model):
        raise Http404
    record = get_object_or_404(model, pk=pk)
    if not image_store.can_edit_images(request.user, record):
        raise PermissionDenied
    files = request.FILES.getlist('images')[:image_store.MAX_FILES]
    added, errors = 0, []
    for f in files:
        try:
            image_store.add_image(record, f, user=request.user, caption=request.POST.get('caption', '').strip())
            added += 1
        except ValidationError as exc:
            errors.extend(exc.messages)
    if added:
        messages.success(request, f'Added {added} image{"s" if added != 1 else ""}.')
    for e in errors:
        messages.error(request, e)
    if not files:
        messages.error(request, 'Pick one or more images to add.')
    return _back(request, record)


@login_required
@require_POST
def image_update(request, pk):
    image = get_object_or_404(Image, pk=pk)
    record = _editable(request, image)
    action = request.POST.get('action')
    if action == 'delete':
        image.delete()
        messages.success(request, 'Image removed.')
    elif action == 'cover':
        image_store.make_cover(image)
        messages.success(request, 'Cover image changed.')
    elif action == 'caption':
        image.caption = request.POST.get('caption', '').strip()[:200]
        image.save(update_fields=['caption'])
    return _back(request, record)
