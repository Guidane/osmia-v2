from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.urls import reverse

from core.trees import TreeNode


class Department(TreeNode):
    """An organisational unit, nested, e.g. ``Operations > Warehouse``."""

    def get_absolute_url(self):
        return reverse('departments:detail', args=[self.pk])

    def members(self, include_sub=False):
        ids = {self.pk, *self.descendant_ids()} if include_sub else {self.pk}
        return get_user_model().objects.filter(department_membership__department_id__in=ids)


class Membership(models.Model):
    """Which department a user works in. Kept here rather than on the user, so
    Users works without this module; the user pages show it through hooks."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='department_membership')
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='memberships')

    def __str__(self):
        return f'{self.user} in {self.department}'

    def audit_record(self):
        return self.department  # logged on the department's page


def department_of(user):
    """The user's department, or None."""
    if user is None or user.pk is None:
        return None
    m = Membership.objects.filter(user=user).select_related('department__parent').first()
    return m.department if m else None


def set_department(user, department):
    if department is None:
        Membership.objects.filter(user=user).delete()
    else:
        Membership.objects.update_or_create(user=user, defaults={'department': department})
