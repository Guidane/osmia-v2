from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.db import models, transaction
from django.urls import reverse

from budgets.models import Budget, TaskBudget


class Project(models.Model):
    """A piece of work with its own budget, that ties the other modules together.

    The project's budget is an ordinary budget (named after the project, in the
    project's department), so everything charged to it or its sub-budgets counts
    as the project's spending: the tasks in the project (their materials from
    stock), and the orders placed on it.
    """

    class Status(models.TextChoices):
        PLANNED = 'planned', 'Planned'
        ACTIVE = 'active', 'Active'
        ON_HOLD = 'on_hold', 'On hold'
        DONE = 'done', 'Done'
        CANCELLED = 'cancelled', 'Cancelled'

    OPEN = (Status.PLANNED, Status.ACTIVE, Status.ON_HOLD)

    code = models.CharField(max_length=30, unique=True, blank=True,
                            help_text='Leave blank to number it automatically (PRJ-0001, ...).')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status, default=Status.PLANNED)
    department = models.ForeignKey('departments.Department', null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='projects', help_text='The department that runs the project; its budget belongs to it.')
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='managed_projects')
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='projects')
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    budget = models.OneToOneField(Budget, null=True, blank=True, on_delete=models.SET_NULL, related_name='project')
    images = GenericRelation('core.Image')  # drawings, quotes, contracts, photos, ...
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
                                   on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.code} · {self.name}' if self.code else self.name

    def get_absolute_url(self):
        return reverse('projects:detail', args=[self.pk])

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.code:
            self.code = f'PRJ-{self.pk:04d}'
            super().save(update_fields=['code'])

    @property
    def is_open(self):
        return self.status in self.OPEN

    def task_ids(self):
        return list(self.task_links.values_list('task_id', flat=True))

    def budget_ids(self):
        """The project's budget and its sub-budgets."""
        return [self.budget_id, *self.budget.descendant_ids()] if self.budget_id else []

    @transaction.atomic
    def set_budget(self, amount, parent=None):
        """Give the project its budget, or update it: named and numbered after the
        project, in its department, under ``parent`` (when given)."""
        budget = self.budget or Budget()
        budget.name, budget.budget_number = self.name, self.code
        budget.amount, budget.department = amount, self.department
        if parent is not None or not budget.pk:
            budget.parent = parent
        budget.save()
        if self.budget_id != budget.pk:
            self.budget = budget
            self.save(update_fields=['budget'])
            for task_id in self.task_ids():  # tasks added before there was a budget
                TaskBudget.objects.update_or_create(task_id=task_id, defaults={'budget': budget})
        return budget

    @transaction.atomic
    def add_task(self, task):
        """Put ``task`` in this project (out of any other) and charge it to the project's budget."""
        ProjectTask.objects.update_or_create(task=task, defaults={'project': self})
        if self.budget_id:
            TaskBudget.objects.update_or_create(task=task, defaults={'budget': self.budget})

    @transaction.atomic
    def remove_task(self, task):
        """Take ``task`` out of the project; it stops being charged to the project's budget."""
        ProjectTask.objects.filter(task=task, project=self).delete()
        TaskBudget.objects.filter(task=task, budget_id__in=self.budget_ids()).delete()


class ProjectTask(models.Model):
    """Puts a task in a project. Kept here so Tasks doesn't depend on Projects."""

    task = models.OneToOneField('tasks.Task', on_delete=models.CASCADE, related_name='project_link')
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='task_links')

    def __str__(self):
        return f'{self.task} → {self.project}'

    def audit_record(self):
        return self.project  # logged in the project's history
