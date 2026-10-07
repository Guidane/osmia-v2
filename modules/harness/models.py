"""Harness designs (from the stand-alone Wire Harness Designer).

A harness project places device instances on a canvas and wires their
connectors together: each harness has a trunk from one device connector and
branches to others, and every branch lists pin-to-pin wires with signal, AWG
and verification flags. The drawing is stored as the designer's JSON; every
save is a new immutable version and ``version`` points at the current one.
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.db import models, transaction
from django.urls import reverse


class SignalRule(models.Model):
    """Two signal types that may be wired together, e.g. RX <-> TX.

    A signal no rule mentions is unrestricted; once a signal appears in a
    rule, only its listed partners are allowed.
    """

    signal_a = models.CharField(max_length=50)
    signal_b = models.CharField(max_length=50)

    class Meta:
        ordering = ['signal_a', 'signal_b']

    def __str__(self):
        return f'{self.signal_a} ↔ {self.signal_b}'

    @classmethod
    def pairs(cls):
        return [[r.signal_a, r.signal_b] for r in cls.objects.all()]

    @classmethod
    @transaction.atomic
    def replace_all(cls, pairs):
        cls.objects.all().delete()
        seen = set()
        for a, b in pairs:
            a, b = str(a).strip()[:50], str(b).strip()[:50]
            key = tuple(sorted((a.upper(), b.upper())))
            if a and b and key not in seen:
                seen.add(key)
                cls.objects.create(signal_a=a, signal_b=b)


class HarnessProject(models.Model):
    name = models.CharField(max_length=200, default='Untitled Harness')
    images = GenericRelation('core.Image')  # pictures, shown with {% image_gallery %}
    version = models.PositiveIntegerField(default=0, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('harness:designer') + f'?project={self.pk}'

    @property
    def current(self):
        return self.versions.filter(version=self.version).first()

    def data(self, version=None):
        snap = self.versions.filter(version=version or self.version).first()
        return snap.data if snap else {'name': self.name, 'instances': [], 'harnesses': []}

    def to_designer(self, version=None):
        data = self.data(version)
        return {**data, 'id': str(self.pk), 'version': version or self.version}

    @transaction.atomic
    def save_version(self, data, user=None):
        payload = {k: v for k, v in data.items() if k not in ('id', 'version', 'versions')}
        payload.setdefault('instances', [])
        payload.setdefault('harnesses', [])
        self.name = (payload.get('name') or 'Untitled Harness')[:200]
        if self.pk is None:
            self.save()
        last = self.versions.order_by('-version').values_list('version', flat=True).first() or 0
        self.version = last + 1
        self.save()
        HarnessProjectVersion.objects.create(project=self, version=self.version, data=payload, created_by=user)
        return self

    def device_ids(self):
        return {str(i.get('device_id')) for i in self.data().get('instances', [])}

    def stats(self):
        data = self.data()
        harnesses = data.get('harnesses', [])
        wires = [c for h in harnesses for b in h.get('branches', []) for c in b.get('connections', [])]
        return {
            'devices': len(data.get('instances', [])),
            'harnesses': len(harnesses),
            'wires': len(wires),
            'verified': sum(1 for w in wires if w.get('verified')),
        }


class HarnessProjectVersion(models.Model):
    audit_log = False  # history/bookkeeping, not an item people change
    project = models.ForeignKey(HarnessProject, on_delete=models.CASCADE, related_name='versions')
    version = models.PositiveIntegerField()
    data = models.JSONField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-version']
        constraints = [models.UniqueConstraint(fields=['project', 'version'], name='unique_project_version')]

    def __str__(self):
        return f'{self.project} v{self.version}'
