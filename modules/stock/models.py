"""Stock: how many of each part are where, and every movement.

Parts (the Parts module) only describe things. Stock keeps:

- **locations** (nested, with labels and codes such as A1A),
- **stock items**: a quantity of a part at a location (a part can be in
  several places; stock that hasn't been put away has no location),
- **stock moves**: receipts, issues, counts and transfers, each with the
  cost it happened at, so tasks and budgets keep what they've spent,
- per part: the reorder level and the average cost of what's been received.
"""
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.urls import reverse

from core.trees import TreeNode


class Location(TreeNode):
    """A place parts are kept, e.g. Warehouse > Rack row A > Rack 1 > Shelf A.

    Its ``code`` joins the labels down the tree (A + 1 + A = "A1A"), which is
    what goes on the shelf. ``path`` and ``code`` are stored so long lists of
    locations don't have to walk up the tree for every row.
    """
    label = models.CharField(max_length=20, blank=True,
                             help_text="Short code, e.g. A or 1. A location's code joins the labels down the tree: "
                                       'rack row A, rack 1, shelf A is A1A.')
    code = models.CharField(max_length=200, blank=True, editable=False, db_index=True)
    path = models.CharField(max_length=500, blank=True, editable=False)
    images = GenericRelation('core.Image')  # e.g. photos of the shelf or bin
    audit_ignore = ('path',)  # follows the names; logging it would repeat every rename

    class Meta(TreeNode.Meta):
        constraints = [models.UniqueConstraint(fields=['parent', 'label'], condition=~models.Q(label=''),
                                               name='unique_location_label_per_parent')]

    def __str__(self):
        path = self.full_path()
        return f'{path} ({self.code})' if self.code else path

    def full_path(self, sep=' > '):
        if self.path and sep == ' > ':
            return self.path
        return super().full_path(sep)

    def get_absolute_url(self):
        return reverse('stock:location_detail', args=[self.pk])

    def refresh_path(self):
        parent = self.parent
        self.path = f'{parent.full_path()} > {self.name}' if parent else self.name
        self.code = (parent.code if parent else '') + self.label

    def save(self, *args, **kwargs):
        old = (self.path, self.code)
        self.refresh_path()
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = {*kwargs['update_fields'], 'path', 'code'}
        super().save(*args, **kwargs)
        if old != (self.path, self.code):
            for child in self.children.all():
                child.parent = self  # the saved one, so the child sees the new path
                child.save()


class PartStock(models.Model):
    """A part's stock settings and value: when it counts as low, and the
    average cost of what's been received (what issues are booked at)."""

    part = models.OneToOneField('inventory.Part', on_delete=models.CASCADE, related_name='stock')
    reorder_level = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                        help_text='Flag as low stock at or below this quantity (all locations together).')
    average_cost = models.DecimalField(max_digits=12, decimal_places=4, default=0,
                                       help_text='Average cost of what has been received, per unit.')

    class Meta:
        verbose_name = 'part stock settings'
        verbose_name_plural = 'part stock settings'

    def __str__(self):
        return f'Stock of {self.part}'

    def audit_record(self):
        return self.part

    @classmethod
    def of(cls, part):
        return cls.objects.get_or_create(part=part)[0]


def qty(value):
    """A quantity as people write it: 7, 2.5 (not 7.00)."""
    value = Decimal(value or 0).normalize()
    return f'{value:f}'


def on_hand(part):
    """All of a part's stock, every location together."""
    return StockItem.objects.filter(part=part).aggregate(total=Sum('quantity'))['total'] or Decimal(0)


def reorder_level(part):
    settings_ = PartStock.objects.filter(part=part).first()
    return settings_.reorder_level if settings_ else Decimal(0)


def is_low(part, total=None):
    """At or below its reorder level (all locations together)."""
    return (on_hand(part) if total is None else total) <= reorder_level(part)


class StockItem(models.Model):
    """How many of a part are at one location (``location`` None: not put away yet)."""

    part = models.ForeignKey('inventory.Part', on_delete=models.CASCADE, related_name='stock_items')
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name='items')
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ['part__part_number', 'location__path']
        constraints = [models.UniqueConstraint(fields=['part', 'location'], name='unique_stock_item')]

    def __str__(self):
        return f'{qty(self.quantity)} {self.part.unit} {self.part.part_number} at {self.location or "no location"}'

    def audit_record(self):
        return self.part

    @property
    def value(self):
        avg = getattr(getattr(self.part, 'stock', None), 'average_cost', Decimal(0)) or Decimal(0)
        return (self.quantity * avg).quantize(Decimal('0.01'))

    @classmethod
    def at(cls, part, location):
        item = cls.objects.filter(part=part, location=location).first()
        return item or cls.objects.create(part=part, location=location)


class StockMove(models.Model):
    class Type(models.TextChoices):
        IN = 'in', 'Receipt'
        OUT = 'out', 'Issue'
        ADJUST = 'adjust', 'Adjustment'
        TRANSFER = 'transfer', 'Transfer'  # moved to another location; quantity unchanged

    part = models.ForeignKey('inventory.Part', on_delete=models.PROTECT, related_name='moves')
    move_type = models.CharField('Type', max_length=10, choices=Type)
    delta = models.DecimalField(max_digits=12, decimal_places=2, help_text='Signed change to quantity on hand.')
    # Where a receipt, issue or count happened.
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL, related_name='moves')
    # The cost per unit when the move happened, so later prices don't rewrite
    # what a task or budget has already spent.
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    note = models.CharField(max_length=255, blank=True)
    # Transfers: where the stock was moved from and to.
    from_location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    to_location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    # Integration with the Tasks module: materials consumed by a task.
    task = models.ForeignKey('tasks.Task', null=True, blank=True, on_delete=models.SET_NULL, related_name='stock_moves')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='stock_moves')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.get_move_type_display()} {self.delta:+} {self.part.unit} {self.part.part_number}'

    def audit_record(self):
        return self.part

    @classmethod
    @transaction.atomic
    def record(cls, part, move_type, delta, location=None, unit_cost=None, **fields):
        """Book a receipt (delta > 0), issue (delta < 0) or count adjustment at
        ``location`` (None: stock not put away). Receipts at a price update the
        part's average cost; issues are booked at the average cost."""
        delta = Decimal(delta)
        settings_ = PartStock.of(part)
        before = on_hand(part)
        item = StockItem.at(part, location)
        if delta < 0 and item.quantity + delta < 0:
            where = f'at {location}' if location else 'without a location'
            raise ValidationError(f'Only {qty(item.quantity)} {part.unit} of {part.part_number} {where}.')
        if unit_cost is None:
            unit_cost = settings_.average_cost
        elif delta > 0 and move_type == cls.Type.IN:
            # Moving average: what's in stock now, plus this receipt at its price.
            stocked = max(before, Decimal(0))
            total = stocked + delta
            settings_.average_cost = ((stocked * settings_.average_cost + delta * Decimal(unit_cost)) / total) if total else Decimal(unit_cost)
            settings_.save(update_fields=['average_cost'])
        move = cls.objects.create(part=part, move_type=move_type, delta=delta, location=location,
                                  unit_cost=Decimal(unit_cost).quantize(Decimal('0.01')), **fields)
        item.quantity += delta
        if item.quantity == 0 and item.location_id is None:
            item.delete()
        else:
            item.save(update_fields=['quantity'])
        after = before + delta
        if after <= settings_.reorder_level < before:
            # Automations: "A part runs low on stock"
            from core import automation
            automation.emit('stock.stock_low', part)
        return move

    @classmethod
    @transaction.atomic
    def transfer(cls, item, location, user=None, note=''):
        """Move a stock item (all of it) to ``location``; it joins any stock of
        the part already there. Returns the move, or None if it's already there."""
        if item.location_id == getattr(location, 'pk', None):
            return None
        part = item.part
        move = cls.objects.create(part=part, move_type=cls.Type.TRANSFER, delta=0,
                                  unit_cost=PartStock.of(part).average_cost, from_location=item.location,
                                  to_location=location, user=user, note=note)
        target = StockItem.objects.filter(part=part, location=location).first()
        if target:
            target.quantity += item.quantity
            target.save(update_fields=['quantity'])
            item.delete()
        else:
            item.location = location
            item.save(update_fields=['location'])
        return move

    @classmethod
    def material_cost_by_task(cls, task_ids):
        """{task id: cost of parts issued to it, net of parts returned}."""
        costs = {}
        moves = cls.objects.filter(task_id__in=task_ids, move_type__in=[cls.Type.IN, cls.Type.OUT])
        for task_id, delta, unit_cost in moves.values_list('task_id', 'delta', 'unit_cost'):
            costs[task_id] = costs.get(task_id, Decimal(0)) - delta * unit_cost
        return {task_id: cost.quantize(Decimal('0.01')) for task_id, cost in costs.items()}
