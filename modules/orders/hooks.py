from collections import defaultdict
from decimal import Decimal

from django.db.models.signals import post_save
from django.dispatch import receiver

from core import automation, hooks
from core.automation import Field

from .models import Order

# -- Automations: triggers ------------------------------------------------------------

ORDER_FIELDS = [
    Field('number', 'Number', lambda o: o.number),
    Field('supplier', 'Supplier', lambda o: o.supplier.name if o.supplier else ''),
    Field('budget', 'Budget', lambda o: o.budget or ''),
    Field('total', 'Total', lambda o: o.total, kind='number'),
]
automation.event('orders.order_placed', 'An order is placed', fields=ORDER_FIELDS)
automation.event('orders.order_received', 'An order is received', fields=ORDER_FIELDS)
automation.status_model(Order)
automation.track(Order, 'status')


@receiver(post_save, sender=Order, dispatch_uid='orders-automation-events')
def order_saved(sender, instance, created, **kwargs):
    change = automation.changed(instance, 'status')
    automation.remember_saved(instance, 'status')
    if created or not change:
        return
    if instance.status == Order.Status.PLACED:
        automation.emit('orders.order_placed', instance)
    elif instance.status == Order.Status.RECEIVED:
        automation.emit('orders.order_received', instance)


# -- Budgets: placed and received orders are spending ---------------------------------

@hooks.register('budget_costs')
def order_costs(budget_ids):
    costs = defaultdict(Decimal)
    for order in Order.objects.filter(budget_id__in=budget_ids, status__in=[Order.Status.PLACED, Order.Status.PARTIAL, Order.Status.RECEIVED]).prefetch_related('lines'):
        costs[order.budget_id] += order.total
    return hooks.Costs('Orders', dict(costs))


@hooks.register('budget_detail_panels')
def budget_orders(request, budget):
    orders = budget.orders.prefetch_related('lines')
    if not orders:
        return None
    return hooks.panel('Orders', 'orders/_budget_panel.html', {'orders': orders}, request)
