from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.urls import reverse

from core import automation, hooks
from core.automation import Field, Param
from inventory.models import Part

from .models import PartStock, StockItem, StockMove, on_hand, qty


@hooks.register('task_costs')
def material_costs(task_ids):
    return hooks.Costs('Materials', StockMove.material_cost_by_task(task_ids))


@hooks.register('dashboard_widgets')
def low_stock(request):
    from .views import low_stock_parts
    count = len(low_stock_parts())
    return hooks.Widget('Low-stock parts', count, reverse('stock:list') + '?low=1', tone='warn' if count else 'ok')


@hooks.register('task_detail_panels')
def task_materials(request, task):
    moves = task.stock_moves.select_related('part', 'user', 'location')
    return hooks.panel('Materials', 'stock/_task_panel.html', {'moves': moves, 'task': task}, request)


@hooks.register('user_detail_panels')
def user_stock_moves(request, user):
    moves = user.stock_moves.select_related('part', 'task', 'location')[:10]
    if not moves:
        return None
    return hooks.panel('Recent stock moves', 'stock/_moves_table.html', {'moves': moves}, request, order=20)


@hooks.register('part_detail_panels')
def part_stock(request, part):
    """A part's stock: where it is, its reorder level and cost, recent moves."""
    from .views import move_locations
    items = list(StockItem.objects.filter(part=part).exclude(quantity=0).select_related('location'))
    settings_ = PartStock.objects.filter(part=part).first()
    total = sum((i.quantity for i in items), Decimal(0))
    return hooks.panel('Stock', 'stock/_part_panel.html', {
        'part': part, 'items': items, 'total': total, 'settings': settings_,
        'low': bool(settings_ and total <= settings_.reorder_level and settings_.reorder_level),
        'moves': part.moves.select_related('user', 'task', 'location', 'from_location', 'to_location')[:20],
        'move_locations': move_locations(),
    }, request, order=1)


# -- Automations: "A part runs low on stock" and "Issue / receive stock" ---------------

automation.event('stock.stock_low', 'A part runs low on stock', fields=[
    Field('part_number', 'Part number', lambda p: p.part_number),
    Field('name', 'Name', lambda p: p.name),
    Field('quantity_on_hand', 'On hand', lambda p: on_hand(p), kind='number'),
])


@automation.action('stock.stock_move', 'Issue / receive stock', params=[
    Param('part', 'Part', 'part', required=True),
    Param('direction', 'Direction', 'select', required=True, choices=(('in', 'Receive into stock'), ('out', 'Issue from stock'))),
    Param('quantity', 'Quantity', 'number', required=True),
    Param('note', 'Note', 'text', help='Use {object} or {source} to name the record, e.g. "Spares for {source}". '
                                       'Receipts go in without a location; issues come from where most of the part is.'),
])
def stock_move(ctx, params):
    part = Part.objects.filter(pk=params.get('part') or None).first()
    if part is None:
        raise ValidationError('The part this rule moves no longer exists.')
    try:
        quantity = Decimal(str(params.get('quantity')))
    except (InvalidOperation, TypeError):
        raise ValidationError('Quantity must be a number.')
    if quantity <= 0:
        raise ValidationError('Quantity must be more than 0.')
    issue = params.get('direction') == 'out'
    location = None
    if issue:
        biggest = StockItem.objects.filter(part=part).order_by('-quantity').first()
        location = biggest.location if biggest else None
    # A task that triggered the rule gets the materials booked against it.
    task = ctx.get('object') if getattr(ctx.get('object'), '_meta', None) and ctx['object']._meta.label == 'tasks.Task' else None
    StockMove.record(
        part, StockMove.Type.OUT if issue else StockMove.Type.IN, -quantity if issue else quantity, location=location,
        note=automation.render(params.get('note') or f'By rule "{getattr(ctx.get("rule"), "name", "")}"', ctx)[:255],
        task=task,
    )
    return f'{"Issued" if issue else "Received"} {qty(quantity)} {part.unit} {part.part_number}'
