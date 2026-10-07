from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.db import models
from django.urls import reverse
from django.utils import timezone


class TaskBatch(models.Model):
    """Tasks created together by an automation rule, e.g. the checks for a
    newly placed order. When the last of them is done the group is complete,
    which rules can react to ("All tasks created by a rule are done")."""
    audit_log = False  # history/bookkeeping, not an item people change

    name = models.CharField(max_length=200)
    rule_id = models.PositiveIntegerField(null=True, blank=True, help_text='The automation rule that created the tasks.')
    # The record the tasks are for (e.g. an order), in whichever module it lives.
    source_type = models.ForeignKey('contenttypes.ContentType', null=True, blank=True, on_delete=models.SET_NULL)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source_label = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} · {self.source_label}' if self.source_label else self.name

    @property
    def source(self):
        if not self.source_type_id or not self.source_id:
            return None
        model = self.source_type.model_class()
        return model.objects.filter(pk=self.source_id).first() if model else None

    def is_complete(self):
        return not self.tasks.exclude(status=Task.Status.DONE).exists()


class Task(models.Model):
    class Status(models.TextChoices):
        TODO = 'todo', 'To do'
        IN_PROGRESS = 'in_progress', 'In progress'
        DONE = 'done', 'Done'

    class Priority(models.IntegerChoices):
        LOW = 0, 'Low'
        NORMAL = 1, 'Normal'
        HIGH = 2, 'High'

    title = models.CharField(max_length=200)
    images = GenericRelation('core.Image')  # pictures, shown with {% image_gallery %}
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status, default=Status.TODO)
    priority = models.IntegerField(choices=Priority, default=Priority.NORMAL)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='assigned_tasks',
    )
    department = models.ForeignKey(
        'departments.Department', null=True, blank=True, on_delete=models.SET_NULL, related_name='tasks',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, editable=False, on_delete=models.SET_NULL, related_name='created_tasks',
    )
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    batch = models.ForeignKey(TaskBatch, null=True, blank=True, editable=False, on_delete=models.SET_NULL, related_name='tasks')

    class Meta:
        ordering = ['-priority', 'due_date', '-created_at']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('tasks:detail', args=[self.pk])

    @property
    def automation_source(self):
        """For rules: the record this task's group was created for (e.g. an order)."""
        return self.batch.source if self.batch_id else None

    @property
    def is_overdue(self):
        return bool(self.due_date and self.status != self.Status.DONE and self.due_date < timezone.localdate())

    @property
    def priority_css(self):
        return {self.Priority.HIGH: 'high', self.Priority.LOW: 'low'}.get(self.priority, '')

    def save(self, *args, **kwargs):
        if self.status == self.Status.DONE and not self.completed_at:
            self.completed_at = timezone.now()
        elif self.status != self.Status.DONE:
            self.completed_at = None
        super().save(*args, **kwargs)
