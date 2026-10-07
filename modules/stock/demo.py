from django.contrib.auth import get_user_model

from core.trees import get_or_create_path
from inventory.models import Part
from tasks.models import Task

from .models import Location, PartStock, StockItem, StockMove

# part number: (location, price, reorder at, opening quantity)
STOCK = {
    'SCR-M3X8': ('Warehouse > Aisle 1 > Bin A1', '0.12', 200, 500),
    'NUT-M3': ('Warehouse > Aisle 1 > Bin A1', '0.05', 200, 150),
    'RES-100R-50W': ('Warehouse > Aisle 1 > Shelf 3', '1.80', 10, 40),
    'RES-0R1-5W': ('Warehouse > Aisle 1 > Shelf 3', '0.95', 20, 60),
    'RES-10K-0603': ('Warehouse > Aisle 2 > Drawer 2', '0.01', 1000, 4000),
    'CAP-470U-63V': ('Warehouse > Aisle 2 > Drawer 3', '0.45', 50, 120),
    'DB25-M': ('Warehouse > Aisle 2 > Drawer 4', '2.40', 10, 30),
    'DB25-F': ('Warehouse > Aisle 2 > Drawer 4', '2.60', 10, 8),
    'DS-PIN-C': ('Warehouse > Aisle 2 > Drawer 5', '0.18', 100, 400),
    'DS-SKT-C': ('Warehouse > Aisle 2 > Drawer 5', '0.21', 100, 350),
    'FUSE-T5A': ('Warehouse > Aisle 2 > Drawer 6', '0.30', 50, 200),
    'PSU-24V-150W': ('Warehouse > Cage', '89.00', 1, 3),
    'PSU-12V-50W': ('Warehouse > Cage', '24.00', 2, 5),
    'PSU-PROG-3K': ('Warehouse > Cage', '1250.00', 1, 1),
    'ESD-STRAP': ('Warehouse > Aisle 2 > Drawer 1', '3.50', 20, 12),
}


def load():
    """Opening stock for the demo parts that have none yet."""
    bob = get_user_model().objects.filter(username='bob').first()
    first_run = not StockMove.objects.exists()
    for part in Part.objects.filter(part_number__in=STOCK):
        path, price, reorder, qty = STOCK[part.part_number]
        location = get_or_create_path(Location, path)
        settings_ = PartStock.of(part)
        if not settings_.reorder_level:
            settings_.reorder_level = reorder
            settings_.save(update_fields=['reorder_level'])
        if not StockItem.objects.filter(part=part).exists() and not part.moves.exists():
            StockMove.record(part, StockMove.Type.IN, qty, location=location, unit_cost=price, user=bob, note='Opening stock')

    repair = Task.objects.filter(title__startswith='Repair returned PDU').first()
    psu = Part.objects.filter(part_number='PSU-24V-150W').first()
    if first_run and repair and psu:
        item = StockItem.objects.filter(part=psu).exclude(quantity=0).first()
        StockMove.record(psu, StockMove.Type.OUT, -1, location=item.location if item else None,
                         user=repair.assignee, task=repair, note='Replacement power supply')
