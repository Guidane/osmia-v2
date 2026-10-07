from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from .forms import ToolForm
from .models import Tool


class ToolListView(LoginRequiredMixin, ListView):
    model = Tool

    def get_queryset(self):
        qs = Tool.objects.select_related('responsible').prefetch_related('images')
        g = self.request.GET
        for word in g.get('q', '').split():
            qs = qs.filter(Q(name__icontains=word) | Q(part_number__icontains=word) | Q(manufacturer__icontains=word)
                           | Q(asset_tag__icontains=word) | Q(storage__icontains=word))
        if g.get('kind') in Tool.Kind.values:
            qs = qs.filter(kind=g['kind'])
        if not g.get('retired'):
            qs = qs.filter(is_active=True)
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, kinds=Tool.Kind.choices)


class ToolDetailView(LoginRequiredMixin, DetailView):
    model = Tool

    def get_context_data(self, **kwargs):
        parts = self.object.parts.select_related('category').prefetch_related('images') if hasattr(self.object, 'parts') else []
        return super().get_context_data(**kwargs, parts=parts)


class ToolFormMixin(LoginRequiredMixin):
    model = Tool
    form_class = ToolForm
    template_name = 'core/form.html'

    def form_valid(self, form):
        messages.success(self.request, 'Tool saved.')
        return super().form_valid(form)


class ToolCreateView(ToolFormMixin, CreateView):
    extra_context = {'heading': 'New tool'}


class ToolUpdateView(ToolFormMixin, UpdateView):
    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, heading=f'Edit {self.object}', cancel_url=self.object.get_absolute_url())
