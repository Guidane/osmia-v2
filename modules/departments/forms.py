from core.trees import TreeNodeForm

from .models import Department


class DepartmentForm(TreeNodeForm):
    class Meta(TreeNodeForm.Meta):
        model = Department
