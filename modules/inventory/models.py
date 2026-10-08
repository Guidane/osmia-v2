from django.contrib.contenttypes.fields import GenericRelation
from django.db import models
from django.urls import reverse

from core.trees import TreeNode


class Category(TreeNode):
    class Meta(TreeNode.Meta):
        verbose_name_plural = 'categories'

    def get_absolute_url(self):
        return reverse('inventory:category_edit', args=[self.pk])


class Attribute(models.Model):
    """An attribute name defined on a category; each part stores its own value."""

    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='attributes')
    name = models.CharField(max_length=100)
    default_value = models.CharField(
        max_length=200, blank=True, help_text='Example or typical value, shown as a hint on parts.',
    )

    class Meta:
        ordering = ['category', 'name']
        constraints = [models.UniqueConstraint(fields=['category', 'name'], name='unique_attribute_per_category')]

    def __str__(self):
        return self.name


class MatingFamily(models.Model):
    """Connectors that mate with each other, e.g. D-sub DB25. Each part in the
    family is on the pin or the socket side and mates with the other side."""

    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'mating families'

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('inventory:family_detail', args=[self.pk])


class Part(models.Model):
    class Side(models.TextChoices):
        PIN = 'pin', 'Pin'
        SOCKET = 'socket', 'Socket'

    part_number = models.CharField(max_length=50, unique=True)
    name = models.CharField('Description', max_length=200, blank=True)
    images = GenericRelation('core.Image')  # pictures, shown with {% image_gallery %}
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name='parts')
    unit = models.CharField(max_length=20, default='pcs')
    is_active = models.BooleanField('Active', default=True, help_text='Untick to archive the part.')
    # How parts go together. Stock (quantities, locations, cost) lives in the Stock module.
    mating_family = models.ForeignKey(
        MatingFamily, null=True, blank=True, on_delete=models.SET_NULL, related_name='parts',
        help_text='The connector family, e.g. D-sub DB25. It mates with the parts of the family on the other side.',
    )
    mating_side = models.CharField('side', max_length=10, choices=Side, blank=True,
                                   help_text='Pin or socket: which side of the family this part is.')
    fits = models.ManyToManyField(
        'self', blank=True, symmetrical=False, related_name='fits_into', verbose_name='accepts',
        help_text='Parts used in or with this one: e.g. a D-sub housing accepts its crimp contacts.',
    )
    tools = models.ManyToManyField('tools.Tool', blank=True, related_name='parts',
                                   help_text='Tools to work with it, e.g. the crimp tool for a contact.')

    class Meta:
        ordering = ['part_number']

    def __str__(self):
        return f'{self.part_number} · {self.name}' if self.name else self.part_number

    def get_absolute_url(self):
        return reverse('inventory:part_detail', args=[self.pk])

    @property
    def opposite_side(self):
        return {self.Side.PIN: self.Side.SOCKET, self.Side.SOCKET: self.Side.PIN}.get(self.mating_side, '')

    @property
    def mates_with(self):
        """The family's parts on the other side (and those whose side isn't set yet)."""
        if not self.mating_family_id:
            return Part.objects.none()
        qs = Part.objects.filter(mating_family_id=self.mating_family_id).exclude(pk=self.pk)
        return qs.exclude(mating_side=self.mating_side) if self.mating_side else qs


class PartAttributeValue(models.Model):
    part = models.ForeignKey(Part, on_delete=models.CASCADE, related_name='attribute_values')
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name='part_values')
    value = models.CharField(max_length=200)

    class Meta:
        ordering = ['attribute__name']
        constraints = [models.UniqueConstraint(fields=['part', 'attribute'], name='unique_value_per_part_attribute')]

    def __str__(self):
        return f'{self.attribute.name}: {self.value}'
