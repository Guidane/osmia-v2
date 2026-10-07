from decimal import Decimal

from django import forms

from core.trees import TreeNodeForm, link_parents, sorted_by_path
from inventory.models import Part
from tasks.models import Task

from .models import Location, PartStock, StockItem, StockMove
from .models import qty as format_qty


class LocationForm(TreeNodeForm):
    class Meta(TreeNodeForm.Meta):
        model = Location
        fields = ['name', 'label', 'parent']

    def clean(self):
        data = super().clean()
        label = (data.get('label') or '').strip()
        data['label'] = label
        if label:
            clash = Location.objects.filter(parent=data.get('parent'), label__iexact=label).exclude(pk=self.instance.pk).first()
            if clash:
                self.add_error('label', f'"{clash.name}" in the same place already has the label {clash.label}.')
        return data


class PartStockForm(forms.ModelForm):
    class Meta:
        model = PartStock
        fields = ['reorder_level']


class StockMoveForm(forms.Form):
    part = forms.ModelChoiceField(Part.objects.filter(is_active=True))
    # Transfers are made by selecting rows and picking a location, not here.
    move_type = forms.ChoiceField(label='Type', choices=[c for c in StockMove.Type.choices if c[0] != StockMove.Type.TRANSFER])
    location = forms.ModelChoiceField(
        Location.objects.none(), required=False, empty_label='No location (not put away)',
        help_text='Where the parts are received, taken from or counted.',
    )
    quantity = forms.DecimalField(
        min_value=Decimal('0'), decimal_places=2,
        help_text='For receipts and issues, the amount moved. For counts, the quantity counted at the location.',
    )
    unit_cost = forms.DecimalField(
        label='Price per unit', required=False, min_value=Decimal('0'), decimal_places=2,
        help_text='Receipts only, optional: updates the part\'s average cost.',
    )
    task = forms.ModelChoiceField(
        Task.objects.exclude(status=Task.Status.DONE), required=False,
        help_text='Optional: the task these materials are for.',
    )
    note = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['location'].queryset = Location.objects.all()
        self.fields['location'].choices = [('', 'No location (not put away)')] + [
            (loc.pk, f'{loc.code} · {loc.full_path()}' if loc.code else loc.full_path())
            for loc in sorted_by_path(link_parents(Location.objects.all()))
        ]

    def clean(self):
        data = super().clean()
        part, move_type, qty = data.get('part'), data.get('move_type'), data.get('quantity')
        if part is None or qty is None:
            return data
        if move_type in (StockMove.Type.IN, StockMove.Type.OUT) and qty == 0:
            self.add_error('quantity', 'Enter a quantity greater than zero.')
        here = StockItem.objects.filter(part=part, location=data.get('location')).first()
        have = here.quantity if here else Decimal(0)
        if move_type == StockMove.Type.OUT and qty > have:
            where = data['location'] if data.get('location') else 'without a location'
            self.add_error('quantity', f'Only {format_qty(have)} {part.unit} at {where}.')
        data['_have'] = have
        return data

    def save(self, user):
        d = self.cleaned_data
        part, qty = d['part'], d['quantity']
        delta = {
            StockMove.Type.IN: qty,
            StockMove.Type.OUT: -qty,
            StockMove.Type.ADJUST: qty - d['_have'],
        }[d['move_type']]
        cost = d.get('unit_cost') if d['move_type'] == StockMove.Type.IN else None
        return StockMove.record(part, d['move_type'], delta, location=d.get('location'), unit_cost=cost,
                                task=d['task'], note=d['note'], user=user)
