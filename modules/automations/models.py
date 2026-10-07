from django.conf import settings
from django.db import models
from django.urls import reverse

from core import automation


class Rule(models.Model):
    """When <trigger> happens, if <conditions> hold, then do <actions>.

    conditions: [{"field": "supplier", "op": "is", "value": "Acme"}, ...]  (all must hold)
    actions:    [{"action": "tasks.create_tasks", "params": {...}}, ...]  (run in order)
    """
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    trigger = models.CharField(max_length=100)
    conditions = models.JSONField(default=list, blank=True)
    actions = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='automation_rules', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('automations:detail', args=[self.pk])

    @property
    def trigger_label(self):
        event = automation.events().get(self.trigger)
        return event.label if event else f'{self.trigger} (no longer available)'

    def action_labels(self):
        known = automation.actions()
        return [known[a['action']].label if a.get('action') in known else f'{a.get("action")} (no longer available)'
                for a in self.actions]


class Run(models.Model):
    """One time a rule fired: what it was about and what it did."""
    audit_log = False  # history/bookkeeping, not an item people change

    class Status(models.TextChoices):
        OK = 'ok', 'Done'
        FAILED = 'failed', 'Failed'

    rule = models.ForeignKey(Rule, on_delete=models.CASCADE, related_name='runs')
    event = models.CharField(max_length=100)
    object_label = models.CharField(max_length=200, blank=True)
    object_url = models.CharField(max_length=300, blank=True)
    source_label = models.CharField(max_length=200, blank=True)
    source_url = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status)
    log = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.rule} · {self.object_label}'


class Notification(models.Model):
    audit_log = False  # history/bookkeeping, not an item people change
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    message = models.CharField(max_length=500)
    url = models.CharField(max_length=300, blank=True)
    rule = models.ForeignKey(Rule, null=True, blank=True, on_delete=models.SET_NULL, related_name='notifications')
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return self.message
