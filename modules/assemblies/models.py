from collections import defaultdict
from decimal import Decimal

from django.contrib.contenttypes.fields import GenericRelation
from django.db import models
from django.db.models import Q
from django.urls import reverse


class Assembly(models.Model):
    """A buildable unit: a bill of materials of parts and/or sub-assemblies,
    plus build and usage instructions, a version and a lifecycle status."""

    class Type(models.TextChoices):
        # "generic" is the plain component list; the others can get their own
        # display later (harness diagram, bolt pattern, ...). A "device" is an
        # electrical unit the company builds (a computer, an inverter, ...); its
        # named connectors (J01, P01, ...) are defined in the Devices module.
        GENERIC = 'generic', 'Generic'
        DEVICE = 'device', 'Device'
        HARNESS = 'harness', 'Harness'
        PLATE_STACK = 'plate_stack', 'Plate stack'

    class Status(models.TextChoices):
        MANUFACTURING = 'manufacturing', 'Manufacturing'
        COMPLETED = 'completed', 'Completed'
        DISCONTINUED = 'discontinued', 'Discontinued'

    name = models.CharField(max_length=200)
    assembly_type = models.CharField('Type', max_length=20, choices=Type, default=Type.GENERIC)
    status = models.CharField(max_length=20, choices=Status, default=Status.MANUFACTURING)
    images = GenericRelation('core.Image')  # pictures, shown with {% image_gallery %}
    version = models.CharField(max_length=20, default='1')
    # Bumped automatically whenever the BOM or version changes, so anything
    # built against an older revision can tell it has drifted.
    revision = models.PositiveIntegerField(default=1, editable=False)
    build_instructions = models.TextField(blank=True)
    usage_instructions = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'assemblies'

    def __str__(self):
        return f'{self.name} v{self.version}'

    def get_absolute_url(self):
        return reverse('assemblies:detail', args=[self.pk])

    def descendant_ids(self):
        """Ids of every assembly reachable through this one's components."""
        ids, frontier = set(), {self.pk}
        while frontier:
            children = set(
                AssemblyComponent.objects.filter(assembly_id__in=frontier, child_assembly__isnull=False)
                .values_list('child_assembly_id', flat=True)
            )
            frontier = children - ids
            ids |= frontier
        return ids

    def ancestor_ids(self):
        """Ids of every assembly that contains this one, directly or not."""
        ids, frontier = set(), {self.pk}
        while frontier:
            parents = set(
                AssemblyComponent.objects.filter(child_assembly_id__in=frontier).values_list('assembly_id', flat=True)
            )
            frontier = parents - ids
            ids |= frontier
        return ids

    def bom_signature(self):
        """Order-independent snapshot of the BOM, for change detection."""
        return sorted(
            (c.part_id or 0, c.child_assembly_id or 0, c.quantity.normalize())
            for c in self.components.all()
        )

    def structure(self):
        """The fully expanded BOM as flat rows ``{depth, component, total}``,
        where ``total`` is the quantity needed for one of this assembly."""
        rows = []

        def walk(assembly, depth, multiplier, path):
            for c in assembly.components.select_related('part', 'child_assembly'):
                total = c.quantity * multiplier
                rows.append({'depth': depth, 'component': c, 'total': total, 'indent': depth * 20})
                child = c.child_assembly
                if child is not None and child.pk not in path:  # path guards against bad data
                    walk(child, depth + 1, total, path | {child.pk})

        walk(self, 0, Decimal(1), {self.pk})
        return rows

    def total_parts(self):
        """Every part needed to build one of this assembly, sub-assemblies
        expanded, with stock on hand and how many builds that stock covers."""
        totals = defaultdict(Decimal)
        parts = {}
        for row in self.structure():
            part = row['component'].part
            if part is not None:
                totals[part.pk] += row['total']
                parts[part.pk] = part
        from django.db.models import Sum

        from stock.models import StockItem
        stocked = dict(StockItem.objects.filter(part_id__in=totals).values_list('part').annotate(t=Sum('quantity')))
        result = []
        for pk, needed in totals.items():
            part = parts[pk]
            have = stocked.get(pk) or Decimal(0)
            result.append({
                'part': part,
                'needed': needed,
                'on_hand': have,
                'short': max(needed - have, Decimal(0)),
                'builds': int(have // needed) if needed else None,
            })
        return sorted(result, key=lambda r: r['part'].part_number)

    def buildable_count(self, totals=None):
        totals = self.total_parts() if totals is None else totals
        counts = [r['builds'] for r in totals if r['builds'] is not None]
        return min(counts) if counts else None


class AssemblyComponent(models.Model):
    """One BOM line: either a part or a sub-assembly, with a quantity."""

    assembly = models.ForeignKey(Assembly, on_delete=models.CASCADE, related_name='components')
    part = models.ForeignKey(
        'inventory.Part', null=True, blank=True, on_delete=models.PROTECT, related_name='assembly_uses',
    )
    child_assembly = models.ForeignKey(
        Assembly, null=True, blank=True, on_delete=models.PROTECT, related_name='used_in',
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=1)

    class Meta:
        ordering = ['id']
        constraints = [
            models.CheckConstraint(
                condition=Q(part__isnull=False, child_assembly__isnull=True)
                | Q(part__isnull=True, child_assembly__isnull=False),
                name='component_is_part_xor_assembly',
            ),
            models.CheckConstraint(condition=Q(quantity__gt=0), name='component_quantity_positive'),
        ]

    def __str__(self):
        return f'{self.quantity.normalize():f} × {self.item}'

    @property
    def item(self):
        return self.part or self.child_assembly

    @property
    def ref(self):
        return f'p:{self.part_id}' if self.part_id else f'a:{self.child_assembly_id}'


class TaskLink(models.Model):
    """Links a task to the assembly it builds or works on.

    Kept in this module (not as a field on Task) so the Tasks module stays
    unaware of assemblies, as with Odoo-style extension.
    """

    task = models.OneToOneField('tasks.Task', on_delete=models.CASCADE, related_name='assembly_link')
    assembly = models.ForeignKey(Assembly, on_delete=models.CASCADE, related_name='task_links')

    def __str__(self):
        return f'{self.task} → {self.assembly}'
