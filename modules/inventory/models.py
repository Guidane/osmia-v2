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


class Part(models.Model):
    part_number = models.CharField(max_length=50, unique=True)
    name = models.CharField('Description', max_length=200, blank=True)
    images = GenericRelation('core.Image')  # pictures, shown with {% image_gallery %}
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name='parts')
    unit = models.CharField(max_length=20, default='pcs')
    is_active = models.BooleanField('Active', default=True, help_text='Untick to archive the part.')
    # How parts go together. Stock (quantities, locations, cost) lives in the Stock module.
    mates_with = models.ManyToManyField(
        'self', blank=True, help_text='Parts this one connects to, both ways: e.g. a 9-pin plug mates with a 9-socket receptacle.',
    )
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



class PartAttributeValue(models.Model):
    part = models.ForeignKey(Part, on_delete=models.CASCADE, related_name='attribute_values')
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name='part_values')
    value = models.CharField(max_length=200)

    class Meta:
        ordering = ['attribute__name']
        constraints = [models.UniqueConstraint(fields=['part', 'attribute'], name='unique_value_per_part_attribute')]

    def __str__(self):
        return f'{self.attribute.name}: {self.value}'
