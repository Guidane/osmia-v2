from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Count
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from core import hooks
from core.trees import sorted_by_path

from .forms import DepartmentForm
from .models import Department


class DepartmentListView(LoginRequiredMixin, ListView):
    model = Department
    template_name = 'departments/department_list.html'  # the queryset becomes a sorted list

    def get_queryset(self):
        return sorted_by_path(
            Department.objects.select_related('parent').annotate(member_count=Count('memberships', distinct=True))
        )


class DepartmentDetailView(LoginRequiredMixin, DetailView):
    model = Department

    def get_context_data(self, **kwargs):
        d = self.object
        return super().get_context_data(
            **kwargs,
            members=d.members(include_sub=True).select_related('department_membership__department'),
            children=sorted_by_path(d.children.all()),
            panels=hooks.collect('department_detail_panels', self.request, d),
        )


class DepartmentCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Department
    form_class = DepartmentForm
    permission_required = 'departments.add_department'
    template_name = 'core/form.html'
    extra_context = {'heading': 'New department'}

    def get_initial(self):
        return {'parent': self.request.GET.get('parent')}


class DepartmentUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Department
    form_class = DepartmentForm
    permission_required = 'departments.change_department'
    template_name = 'core/form.html'

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, heading=f'Edit {self.object}', cancel_url=self.object.get_absolute_url())
