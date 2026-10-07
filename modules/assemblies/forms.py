from decimal import Decimal

from django import forms

from inventory.models import Part

from .models import Assembly


class AssemblyForm(forms.ModelForm):
    class Meta:
        model = Assembly
        fields = ['name', 'assembly_type', 'status', 'version', 'build_instructions', 'usage_instructions']
        widgets = {
            'build_instructions': forms.Textarea(attrs={'rows': 6}),
            'usage_instructions': forms.Textarea(attrs={'rows': 4}),
        }


def allowed_sub_assemblies(assembly):
    """Assemblies that can go into this one: not itself, nor any that already
    contain it (that would make the nesting loop)."""
    assemblies = Assembly.objects.all()
    if assembly.pk:
        assemblies = assemblies.exclude(pk__in={assembly.pk, *assembly.ancestor_ids()})
    return assemblies


class ComponentForm(forms.Form):
    """One BOM row: ``p:<id>`` for a part (picked with the part picker) or
    ``a:<id>`` for a sub-assembly."""
    component = forms.CharField(required=False, widget=forms.HiddenInput)
    quantity = forms.DecimalField(min_value=Decimal('0.001'), decimal_places=3, initial=1, required=False)

    def __init__(self, *args, allowed_assemblies=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.allowed_assemblies = set(allowed_assemblies)
        self.fields['quantity'].widget.attrs.update(step='any', style='width: 7em')

    def clean_component(self):
        ref = (self.cleaned_data.get('component') or '').strip()
        if not ref:
            return ''
        kind, _, obj_id = ref.partition(':')
        if kind == 'p' and obj_id.isdigit() and Part.objects.filter(pk=obj_id).exists():
            return ref
        if kind == 'a' and obj_id.isdigit() and int(obj_id) in self.allowed_assemblies:
            return ref
        raise forms.ValidationError("That component can't be used here.")

    def clean(self):
        data = super().clean()
        if data.get('component') and not data.get('quantity'):
            self.add_error('quantity', 'Enter a quantity.')
        return data

    def ref(self):
        return str((self.data.get(self.add_prefix('component')) if self.is_bound else self.initial.get('component')) or '')

    def target(self):
        """The part or assembly the row stands for, for showing it."""
        kind, _, obj_id = self.ref().partition(':')
        if not obj_id.isdigit():
            return None
        model = Part if kind == 'p' else Assembly if kind == 'a' else None
        return model.objects.filter(pk=obj_id).first() if model else None

    def is_assembly(self):
        return self.ref().startswith('a:')


class BaseComponentFormSet(forms.BaseFormSet):
    def clean(self):
        if any(self.errors):
            return
        seen = set()
        for form in self.forms:
            if self.can_delete and self._should_delete_form(form):
                continue
            ref = form.cleaned_data.get('component')
            if not ref:
                continue
            if ref in seen:
                form.add_error('component', 'Listed twice. Combine the quantities on one row.')
            seen.add(ref)

    def lines(self):
        """(ref, quantity) for every filled-in, non-deleted row."""
        for form in self.forms:
            if self.can_delete and self._should_delete_form(form):
                continue
            if form.cleaned_data.get('component'):
                yield form.cleaned_data['component'], form.cleaned_data['quantity']


ComponentFormSet = forms.formset_factory(ComponentForm, formset=BaseComponentFormSet, extra=0, can_delete=True)


class LinkTaskForm(forms.Form):
    assembly = forms.ModelChoiceField(Assembly.objects.all(), required=False, empty_label='(none)')
