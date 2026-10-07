from decimal import Decimal

from django import forms
from django.db.models import Q

from budgets.models import Budget
from inventory.models import Part
from vendors.models import Vendor

from .models import Order, OrderLine


class SubBudgetSelect(forms.Select):
    """Options carry their parent budget, so the page can list only the chosen parent's sub-budgets."""

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        if getattr(value, 'instance', None):
            option['attrs']['data-parent'] = value.instance.parent_id or ''
        return option


class OrderForm(forms.ModelForm):
    """The budget is picked in two steps: a parent budget, then one of its
    sub-budgets. Leaving the sub-budget empty charges the parent itself."""

    parent_budget = forms.ModelChoiceField(
        Budget.objects.none(), required=False, label='Parent budget',
        help_text='Pick the parent budget, then one of its sub-budgets.',
    )

    class Meta:
        model = Order
        fields = ['supplier', 'parent_budget', 'budget', 'notes']
        labels = {'budget': 'Sub-budget'}
        help_texts = {'budget': 'Leave empty to charge the parent budget itself.'}
        widgets = {'notes': forms.Textarea(attrs={'rows': 3}), 'budget': SubBudgetSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Parents: budgets with sub-budgets, plus top-level ones (which may have none).
        parents = Budget.objects.filter(Q(children__isnull=False) | Q(parent=None)).distinct()
        self.fields['parent_budget'].queryset = parents.select_related('parent__parent')
        self.fields['budget'].queryset = Budget.objects.exclude(parent=None).select_related('parent__parent')
        self.fields['budget'].label_from_instance = lambda b: f'{b.budget_number} · {b.name}' if b.budget_number else b.name
        budget = self.instance.budget
        if budget and not self.is_bound:
            if budget.parent_id and not budget.children.exists():
                self.initial.update(parent_budget=budget.parent_id, budget=budget.pk)
            else:  # charged to a parent budget itself
                self.initial.update(parent_budget=budget.pk, budget=None)
        # Inactive vendors can't be picked for new orders, but an order keeps the one it has.
        self.fields['supplier'].queryset = Vendor.objects.filter(Q(is_active=True) | Q(pk=self.instance.supplier_id))

    def clean(self):
        cleaned = super().clean()
        parent, sub = cleaned.get('parent_budget'), cleaned.get('budget')
        if parent and sub and sub.parent_id != parent.pk:
            self.add_error('budget', f'{sub.name} is not a sub-budget of {parent.name}.')
        elif parent and not sub:
            cleaned['budget'] = parent
        return cleaned


class OrderLineForm(forms.ModelForm):
    class Meta:
        model = OrderLine
        fields = ['part', 'quantity', 'unit_price']
        widgets = {'part': forms.HiddenInput}  # picked with the part picker below the lines

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['part'].queryset = Part.objects.all()
        self.fields['quantity'].min_value = Decimal('0.01')
        self.fields['quantity'].widget.attrs.update(step='any', min='0.01')
        self.fields['unit_price'].widget.attrs.update(step='any', min='0', placeholder='0.00')
        self.fields['unit_price'].required = False
        if not self.instance.pk:
            # A new line starts blank (price shown as a 0.00 hint), so a row left empty isn't taken as filled in.
            self.initial['unit_price'] = None

    def clean_unit_price(self):
        return self.cleaned_data.get('unit_price') or Decimal(0)


    def part_label(self):
        """The line's part as text, also for a new line picked on the page."""
        part = getattr(self.instance, 'part', None) if self.instance.part_id else None
        if part is None and self.is_bound:
            value = self.data.get(self.add_prefix('part'))
            part = Part.objects.filter(pk=value).first() if str(value or '').isdigit() else None
        return part


OrderLineFormSet = forms.inlineformset_factory(Order, OrderLine, form=OrderLineForm, extra=0, can_delete=True)
