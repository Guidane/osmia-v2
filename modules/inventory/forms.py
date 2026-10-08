from django import forms

from core.trees import TreeNodeForm

from .models import Attribute, Category, MatingFamily, Part, PartAttributeValue


class CategoryForm(TreeNodeForm):
    class Meta(TreeNodeForm.Meta):
        model = Category


AttributeFormSet = forms.inlineformset_factory(
    Category, Attribute, fields=['name', 'default_value'], extra=2, can_delete=True,
)


class MatingFamilyForm(forms.ModelForm):
    class Meta:
        model = MatingFamily
        fields = ['name', 'description']


class PartForm(forms.ModelForm):
    """Part fields plus one ``attr_<id>`` field per category attribute.

    Fields for every category are rendered; the page shows only those of the
    selected category, and only those are saved.
    """

    BASE_FIELDS = ['part_number', 'name', 'category', 'unit', 'is_active']
    LINK_FIELDS = ['mating_family', 'mating_side', 'fits', 'tools']

    class Meta:
        model = Part
        fields = ['part_number', 'name', 'category', 'unit', 'is_active', 'mating_family', 'mating_side', 'fits', 'tools']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = Category.objects.select_related('parent')
        others = Part.objects.exclude(pk=self.instance.pk) if self.instance.pk else Part.objects.all()
        # Picked with the part picker on the page; the select holds the choice.
        self.fields['fits'].queryset = others
        self.fields['fits'].widget.attrs.update({'hidden': True, 'class': 'part-links'})
        self.fields['mating_family'].empty_label = 'Not a connector'
        # Active tools, plus retired ones the part already uses.
        from tools.models import Tool
        tools = Tool.objects.filter(is_active=True)
        if self.instance.pk:
            tools = (tools | self.instance.tools.all()).distinct()
        self.fields['tools'].widget = forms.CheckboxSelectMultiple()
        self.fields['tools'].queryset = tools
        current = {}
        if self.instance.pk:
            current = {v.attribute_id: v.value for v in self.instance.attribute_values.all()}
        self.attribute_groups = []
        attributes = Attribute.objects.select_related('category__parent').order_by('category_id', 'name')
        for attr in attributes:
            name = f'attr_{attr.pk}'
            self.fields[name] = forms.CharField(
                label=attr.name, required=False, max_length=200, initial=current.get(attr.pk, ''),
                widget=forms.TextInput(attrs={'placeholder': attr.default_value, 'data-attr-name': attr.name}),
            )
            if not self.attribute_groups or self.attribute_groups[-1]['category'] != attr.category:
                self.attribute_groups.append({'category': attr.category, 'fields': []})
            self.attribute_groups[-1]['fields'].append(name)

    def base_fields_bound(self):
        return [self[name] for name in self.BASE_FIELDS]

    def attribute_fields_bound(self):
        return [
            {'category': g['category'], 'fields': [self[n] for n in g['fields']]}
            for g in self.attribute_groups
        ]

    def clean(self):
        data = super().clean()
        if data.get('mating_family') and not data.get('mating_side'):
            self.add_error('mating_side', 'Pick pin or socket: it mates with the other side of the family.')
        if not data.get('mating_family'):
            data['mating_side'] = ''
        return data

    def save(self, commit=True):
        part = super().save(commit=commit)
        if commit:
            self.save_attribute_values(part)
        return part

    def fits_linked(self):
        return self.linked('fits')

    def linked(self, name):
        """The parts currently picked in a link field, for the page to show."""
        if self.is_bound:
            ids = [i for i in self.data.getlist(name) if str(i).isdigit()] if hasattr(self.data, 'getlist') else []
            return list(Part.objects.filter(pk__in=ids))
        return list(getattr(self.instance, name).all()) if self.instance.pk else []

    def save_attribute_values(self, part):
        valid = set(part.category.attributes.values_list('pk', flat=True)) if part.category else set()
        # Values for attributes outside the part's category no longer apply.
        part.attribute_values.exclude(attribute_id__in=valid).delete()
        for attr_id in valid:
            value = self.cleaned_data.get(f'attr_{attr_id}', '').strip()
            if value:
                PartAttributeValue.objects.update_or_create(part=part, attribute_id=attr_id, defaults={'value': value})
            else:
                part.attribute_values.filter(attribute_id=attr_id).delete()


class QuickPartForm(forms.ModelForm):
    """Just enough to start a part on the fly (e.g. while making an order)."""

    class Meta:
        model = Part
        fields = ['part_number', 'name']

    def clean_part_number(self):
        number = self.cleaned_data['part_number'].strip()
        if Part.objects.filter(part_number__iexact=number).exists():
            raise forms.ValidationError(f'There is already a part {number}.')
        return number
