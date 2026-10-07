from budgets.models import Budget
from inventory.models import Part
from vendors.models import Vendor

from .models import Order, OrderLine


def load():
    """A draft order to try placing (the demo automation rules react to it)."""
    if Order.objects.exists():
        return
    parts = {p.part_number: p for p in Part.objects.all()}
    order = Order.objects.create(supplier=Vendor.objects.filter(name='Connector Supply Co.').first(), budget=Budget.objects.filter(budget_number='B-OPS-WH').first(),
                                 notes='Restock D-sub connectors for the PDU line.')
    for number, qty, price in (('DB25-F', 20, '2.60'), ('DB25-M', 10, '2.40')):
        if number in parts:
            OrderLine.objects.create(order=order, part=parts[number], quantity=qty, unit_price=price)
