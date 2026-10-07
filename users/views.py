from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils.safestring import mark_safe
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from core import hooks

from .forms import ProfileForm, UserCreateForm, UserEditForm
from .models import User


class UserListView(LoginRequiredMixin, ListView):
    model = User
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset().prefetch_related('images')
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
                | Q(email__icontains=q) | Q(job_title__icontains=q)
            )
        for f in hooks.collect('user_list_filters', self.request):  # e.g. by department
            qs = f.apply(qs)
        if self.request.GET.get('show') != 'all':
            qs = qs.filter(is_active=True)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['filters'] = [mark_safe(f.html) for f in hooks.collect('user_list_filters', self.request)]
        # Columns from other modules: [(title, {user pk: html})], filled per page in one go.
        people = list(ctx['object_list'])
        columns = hooks.collect('user_list_columns', self.request)
        ctx['extra_columns'] = [c.title for c in columns]
        cells = [c.cells(people) for c in columns]
        ctx['rows'] = [(p, [mark_safe(c.get(p.pk, '')) for c in cells]) for p in people]
        return ctx


class UserDetailView(LoginRequiredMixin, DetailView):
    model = User
    context_object_name = 'person'  # `user` is the logged-in user in templates

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['panels'] = hooks.collect('user_detail_panels', self.request, self.object)
        ctx['facts'] = [(f.label, mark_safe(f.html)) for f in hooks.collect('user_detail_facts', self.request, self.object)]
        ctx['can_edit'] = self.request.user.has_perm('users.change_user') or self.request.user == self.object
        return ctx


class ExtensionFormsMixin:
    """Fields other modules add to the user form (e.g. the department), as extra
    forms that are checked and saved together with the user."""

    def get_extension_forms(self):
        if not hasattr(self, '_extension_forms'):
            user = self.object if getattr(self, 'object', None) and self.object.pk else None
            self._extension_forms = hooks.collect('user_form_extensions', self.request, user)
        return self._extension_forms

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, extra_forms=self.get_extension_forms())

    def form_valid(self, form):
        extensions = self.get_extension_forms()
        if not all([f.is_valid() for f in extensions]):
            return self.form_invalid(form)
        with transaction.atomic():
            response = super().form_valid(form)
            for f in extensions:
                f.save(self.object)
        messages.success(self.request, self.success_message)
        return response


class UserCreateView(LoginRequiredMixin, PermissionRequiredMixin, ExtensionFormsMixin, CreateView):
    model = User
    form_class = UserCreateForm
    permission_required = 'users.add_user'
    template_name = 'core/form.html'
    extra_context = {'heading': 'New user'}
    success_message = 'User created.'


class UserUpdateView(LoginRequiredMixin, ExtensionFormsMixin, UpdateView):
    model = User
    template_name = 'core/form.html'
    success_message = 'Saved.'

    def get_form_class(self):
        if self.request.user.has_perm('users.change_user'):
            return UserEditForm
        if self.request.user == self.get_object():
            return ProfileForm
        raise PermissionDenied

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['heading'] = f'Edit {self.object}'
        ctx['cancel_url'] = self.object.get_absolute_url()
        return ctx
