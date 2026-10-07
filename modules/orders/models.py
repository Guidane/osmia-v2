"""Purchase orders, as in Waggle V3: a supplier (a vendor), a budget to spend against and
lines of parts. Receiving books the lines that came in into stock, each at the
location it's put away at; lines still to come keep the order open."""
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone


class Order(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PLACED = 'placed', 'Placed'
        PARTIAL = 'partial', 'Partly received'
        RECEIVED = 'received', 'Received'
        CANCELLED = 'cancelled', 'Cancelled'

    # Which status can follow which.
    NEXT = {
        Status.DRAFT: {Status.PLACED, Status.CANCELLED},
        Status.PLACED: {Status.PARTIAL, Status.RECEIVED, Status.CANCELLED},
        Status.PARTIAL: {Status.RECEIVED},
        Status.RECEIVED: set(),
        Status.CANCELLED: set(),
    }

    number = models.CharField(max_length=30, unique=True, blank=True, editable=False)
    supplier = models.ForeignKey('vendors.Vendor', null=True, blank=True, on_delete=models.PROTECT, related_name='orders')
    budget = models.ForeignKey('budgets.Budget', null=True, blank=True, on_delete=models.SET_NULL, related_name='orders')
    status = models.CharField(max_length=20, choices=Status, default=Status.DRAFT, editable=False)
    notes = models.TextField(blank=True)
    images = GenericRelation('core.Image')  # shipping documents: delivery notes, packing slips, ...
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='orders', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    placed_at = models.DateTimeField(null=True, blank=True, editable=False)
    received_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.number} · {self.supplier}' if self.supplier else self.number or 'New order'

    def get_absolute_url(self):
        return reverse('orders:detail', args=[self.pk])

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.number:
            self.number = f'PO-{self.pk:04d}'
            super().save(update_fields=['number'])

    @property
    def total(self):
        return sum((line.line_total for line in self.lines.all()), Decimal(0))

    @property
    def counts_against_budget(self):
        return self.status in (self.Status.PLACED, self.Status.PARTIAL, self.Status.RECEIVED)

    @property
    def can_receive(self):
        return self.status in (self.Status.PLACED, self.Status.PARTIAL)

    def open_lines(self):
        return self.lines.filter(received_at=None)

    def can_become(self, status):
        return status in self.NEXT.get(self.status, set())

    @transaction.atomic
    def set_status(self, value, user=None):
        """Move the order on (also used by automation rules). Receiving books
        every line's parts into stock, once."""
        if value == self.status:
            return
        if not self.can_become(value):
            raise ValidationError(f'{self.number} is {self.get_status_display().lower()}, so it can\'t become '
                                  f'{self.Status(value).label.lower()}.')
        if value == self.Status.PLACED and not self.lines.exists():
            raise ValidationError(f'{self.number} has no lines to order.')
        if value == self.Status.PLACED:
            self.placed_at = timezone.now()
        if value == self.Status.RECEIVED:
            # Everything still open comes in (at the location picked for each line, if any).
            self.receive({line: line.location for line in self.open_lines().select_related('part', 'location')}, user=user)
            return
        if value == self.Status.PARTIAL:
            raise ValidationError('An order is partly received by receiving some of its lines.')
        self.status = value
        self.save()

    @transaction.atomic
    def receive(self, lines, user=None):
        """Book ``lines`` ({line: location or None}) into stock, each once, and
        move the order on: Received when every line is in, else Partly received."""
        if not self.can_receive:
            raise ValidationError(f'{self.number} is {self.get_status_display().lower()}, so there is nothing to receive.')
        from core import audit
        from stock.models import StockMove
        now = timezone.now()
        with audit.acting('orders', source=f'received {self.number}'):
            for line, location in lines.items():
                if line.order_id != self.pk or line.received_at:
                    continue
                StockMove.record(line.part, StockMove.Type.IN, line.quantity, location=location,
                                 unit_cost=line.unit_price, user=user, note=f'Received with {self.number}')
                line.location, line.received_at = location, now
                line.save(update_fields=['location', 'received_at'])
        if self.open_lines().exists():
            self.status = self.Status.PARTIAL
        else:
            self.status = self.Status.RECEIVED
            self.received_at = now
        self.save()


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='lines')
    part = models.ForeignKey('inventory.Part', on_delete=models.PROTECT, related_name='order_lines')
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Filled in when the line is received: where it was put away, and when.
    location = models.ForeignKey('stock.Location', null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name='order_lines', editable=False)
    received_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{Decimal(str(self.quantity)).normalize():f} × {self.part}'

    @property
    def line_total(self):
        return (self.quantity or 0) * (self.unit_price or 0)
