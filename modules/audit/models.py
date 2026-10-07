from django.conf import settings
from django.db import models
from django.urls import reverse


class LogEntry(models.Model):
    """One change to a module's record, written by core.audit."""

    class Action(models.TextChoices):
        CREATED = 'created', 'Created'
        CHANGED = 'changed', 'Changed'
        DELETED = 'deleted', 'Deleted'

    module = models.CharField(max_length=50, db_index=True)  # the module whose record changed
    content_type = models.ForeignKey('contenttypes.ContentType', null=True, on_delete=models.SET_NULL, related_name='+')
    object_id = models.PositiveBigIntegerField(default=0)
    object_label = models.CharField(max_length=200)
    object_url = models.CharField(max_length=300, blank=True)
    # For child rows (an order line, a pin): what it was. The entry is shown on the parent.
    item_type = models.CharField(max_length=100, blank=True)
    item_label = models.CharField(max_length=200, blank=True)
    action = models.CharField(max_length=10, choices=Action)
    changes = models.JSONField(default=list, blank=True)  # [{"field", "old", "new"}]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    # The chain: which module's code made the change, the modules it came
    # through (e.g. "tasks > automations") and an id shared by the whole chain.
    actor_module = models.CharField(max_length=50, blank=True)
    chain_path = models.CharField(max_length=200, blank=True)
    chain_id = models.CharField(max_length=12, blank=True, db_index=True)
    source = models.CharField(max_length=200, blank=True)  # e.g. 'rule "Check in placed orders"'
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['content_type', 'object_id'])]
        verbose_name_plural = 'log entries'

    def __str__(self):
        return f'{self.get_action_display()} {self.object_label}'

    @property
    def triggered_by(self):
        """The other module that caused this change, if any (the "chain" tag)."""
        return self.actor_module if self.actor_module and self.actor_module != self.module else ''

    @property
    def chain_modules(self):
        return [m for m in self.chain_path.split(' > ') if m]

    def chain_url(self):
        return reverse('audit:chain', args=[self.chain_id]) if self.chain_id else ''

    def history_url(self):
        return reverse('audit:history', args=[self.content_type_id, self.object_id]) if self.content_type_id else ''


class HiddenHistory(models.Model):
    """A module whose pages don't show the change history (the History panel
    and the Log link). Its changes are still logged and shown in Audit."""
    module = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.module

    @classmethod
    def shown(cls, module):
        return not cls.objects.filter(module=module).exists()
