"""The user's department on the Users pages: details, list column and filter, and form field."""
from django import forms
from django.template.loader import render_to_string
from django.utils.html import format_html

from core import hooks
from core.trees import sorted_by_path

from .models import Department, Membership, department_of, set_department


def _link(department):
    return format_html('<a href="{}">{}</a>', department.get_absolute_url(), department) if department else '—'


@hooks.register('user_detail_facts')
def department_fact(request, user):
    return hooks.Fact('Department', _link(department_of(user)), order=10)


@hooks.register('user_list_columns')
def department_column(request):
    def cells(users):
        rows = Membership.objects.filter(user__in=users).select_related('department__parent')
        return {m.user_id: _link(m.department) for m in rows}
    return hooks.Column('Department', cells, order=10)


@hooks.register('user_list_filters')
def department_filter(request):
    picked = Department.objects.filter(pk=request.GET.get('department') or None).first()
    html = render_to_string('departments/_user_filter.html', {
        'departments': sorted_by_path(Department.objects.select_related('parent')), 'picked': picked,
    })

    def apply(qs):
        if picked is None:
            return qs
        return qs.filter(department_membership__department_id__in={picked.pk, *picked.descendant_ids()})
    return hooks.ListFilter(html, apply, order=10)


class UserDepartmentForm(forms.Form):
    department = forms.ModelChoiceField(Department.objects.select_related('parent__parent'), required=False)

    def save(self, user):
        set_department(user, self.cleaned_data['department'])


@hooks.register('user_form_extensions')
def department_field(request, user):
    # Departments are assigned by managers; people editing their own profile don't see it.
    if not request.user.has_perm('users.change_user'):
        return None
    initial = {'department': department_of(user)} if user else {}
    return UserDepartmentForm(request.POST or None, prefix='dept', initial=initial)
