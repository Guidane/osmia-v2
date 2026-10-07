from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.db import models
from django.urls import reverse


class Tool(models.Model):
    """A tool used to work with parts, e.g. the crimp tool for D-sub contacts.

    Parts say which tools they're used with (Parts > "Tools"); a tool's page
    lists those parts.
    """

    class Kind(models.TextChoices):
        CRIMP = 'crimp', 'Crimp tool'
        INSERTION = 'insertion', 'Insertion / extraction tool'
        STRIPPER = 'stripper', 'Wire stripper'
        CUTTER = 'cutter', 'Cutter'
        SOLDERING = 'soldering', 'Soldering'
        MEASURING = 'measuring', 'Measuring / test'
        HAND = 'hand', 'Hand tool'
        OTHER = 'other', 'Other'

    name = models.CharField(max_length=200)
    part_number = models.CharField(max_length=100, blank=True, help_text="The manufacturer's part / order number.")
    kind = models.CharField('Type', max_length=20, choices=Kind, default=Kind.OTHER)
    manufacturer = models.CharField(max_length=100, blank=True)
    asset_tag = models.CharField(max_length=100, blank=True, help_text='Our own number, if the tool is labelled.')
    storage = models.CharField('Kept at', max_length=200, blank=True, help_text='Where to find it, e.g. "Bench 2, drawer 3".')
    responsible = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='tools', help_text='Who looks after it.')
    calibration_due = models.DateField(null=True, blank=True, help_text='For tools that need calibrating.')
    notes = models.TextField(blank=True, help_text='e.g. which die or setting to use.')
    is_active = models.BooleanField('Active', default=True, help_text='Untick when the tool is retired.')
    images = GenericRelation('core.Image')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.part_number})' if self.part_number else self.name

    def get_absolute_url(self):
        return reverse('tools:detail', args=[self.pk])
