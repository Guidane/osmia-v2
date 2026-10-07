"""
The first start: until there's an administrator, every page is the setup page.

It makes the administrator account and, on a real install, queues the modules
to install (with or without demo data), so a new Osmia is ready in one step.
"""
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import redirect, render
from django.urls import reverse

from osmia import addons

_ready = False  # once there's an administrator, there always is; skip the query


def has_admin():
    global _ready
    if not _ready:
        _ready = get_user_model().objects.filter(is_superuser=True, is_active=True).exists()
    return _ready


class SetupMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not has_admin():
            allowed = (reverse('core:setup'), '/' + settings.STATIC_URL.lstrip('/'))
            if not request.path.startswith(allowed):
                return redirect('core:setup')
        return self.get_response(request)


class SetupForm(UserCreationForm):
    modules = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple,
                                        help_text='Modules a module needs are installed with it. '
                                                  'You can add or remove modules later on the Modules page.')
    demo = forms.BooleanField(required=False, label='Load demo data (example people, parts, tasks, ...)',
                              help_text='Only for trying Osmia out: don\'t use it on a database for real work.')

    class Meta:
        model = get_user_model()
        fields = ['username', 'first_name', 'last_name', 'email']

    def __init__(self, *args, offer_modules=True, **kwargs):
        super().__init__(*args, **kwargs)
        if offer_modules:
            bundled = addons.bundled_manifests()
            order = sorted(bundled.values(), key=lambda m: (m['sequence'], m['title']))
            self.fields['modules'].choices = [(m['label'], f'{m["title"]}: {m["description"]}') for m in order]
            self.initial.setdefault('modules', [m['label'] for m in order])
        else:
            del self.fields['modules']


def setup(request):
    from .management.commands.load_demo import load_demo
    from .module_views import queue_change, can_change, plan_bundled_install

    if has_admin():
        return redirect('core:home')
    offer_modules = can_change()
    form = SetupForm(request.POST or None, offer_modules=offer_modules)
    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        user.is_staff = user.is_superuser = True
        user.save()
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        demo = form.cleaned_data['demo']
        labels = form.cleaned_data.get('modules') or []
        if offer_modules and labels:
            try:
                ops = plan_bundled_install(labels)
                return queue_change(request, ops, demo=['users', *[op['label'] for op in ops]] if demo else [])
            except addons.PackageError as exc:
                messages.error(request, f'The modules could not be installed: {exc} Install them on the Modules page.')
                return redirect('core:modules')
        if demo:
            load_demo(None if not offer_modules else {'users'})
        return redirect('core:home')
    return render(request, 'core/setup.html', {'form': form})
