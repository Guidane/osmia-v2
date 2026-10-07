from django import forms
from django.contrib.auth import get_user_model

from assemblies.models import Assembly
from inventory.models import Part

from .models import Connector, Device, Pin, Signal, TagOption


class DeviceForm(forms.ModelForm):
    class Meta:
        model = Device
        fields = [
            'name', 'part_number', 'origin', 'role', 'assembly', 'manufacturer', 'model_number', 'asset_tag',
            'color', 'responsible', 'notes',
        ]
        widgets = {'color': forms.TextInput(attrs={'type': 'color'}), 'notes': forms.Textarea(attrs={'rows': 3})}
        help_texts = {'assembly': 'For devices we build: the assembly (type "Device") with its bill of materials.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        taken = Device.objects.exclude(pk=self.instance.pk).exclude(assembly=None).values('assembly_id')
        self.fields['assembly'].queryset = Assembly.objects.filter(assembly_type=Assembly.Type.DEVICE).exclude(pk__in=taken)
        self.fields['responsible'].queryset = get_user_model().objects.filter(is_active=True)

    def clean(self):
        data = super().clean()
        if data.get('origin') == Device.Origin.EXTERNAL and data.get('assembly'):
            self.add_error('assembly', 'External devices are not built by us, so they have no assembly.')
        return data


class ConnectorForm(forms.ModelForm):
    class Meta:
        model = Connector
        fields = ['designator', 'side', 'part', 'description', 'tag_columns']
        widgets = {'tag_columns': forms.HiddenInput}  # set with "+ Tag column" in the pin table

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['part'].queryset = Part.objects.filter(is_active=True).select_related('category')
        self.fields['tag_columns'].required = False
        if self.instance.pk:
            self.initial['tag_columns'] = self.instance.tag_column_count()

    def clean_tag_columns(self):
        return min(Connector.MAX_TAG_COLUMNS, max(1, self.cleaned_data.get('tag_columns') or 1))

    def clean_designator(self):
        designator = self.cleaned_data['designator'].strip().upper()
        clash = Connector.objects.filter(device=self.instance.device, designator=designator).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(f'This device already has a {designator}.')
        return designator


ADD_NEW = '__new__'  # the "+ Add new…" entry in a tag dropdown (turned into a real value by the page's script)


class PinForm(forms.ModelForm):
    signal = forms.ChoiceField(required=False)

    class Meta:
        model = Pin
        fields = ['label', 'signal', *Pin.TAG_FIELDS, 'set_number', 'set_type']
        widgets = {
            'label': forms.TextInput(attrs={'style': 'width: 6em'}),
            'set_number': forms.NumberInput(attrs={'style': 'width: 5em', 'min': 1}),
        }

    def __init__(self, *args, signals=None, tag_options=None, **kwargs):
        super().__init__(*args, **kwargs)
        names = list(signals if signals is not None else Signal.objects.values_list('name', flat=True))
        current = self.instance.signal if self.instance.pk else ''
        if current and current not in names:
            names.append(current)  # an old value that isn't in the list yet
        self.fields['signal'].choices = [('', '—')] + [(n, n) for n in names]

        options = tag_options if tag_options is not None else TagOption.names_for(self.instance.connector if self.instance.connector_id else None)
        for column, field in enumerate(Pin.TAG_FIELDS, start=1):
            values = list(options.get(column, []))
            # Keep the pin's own value, and one just added on the page, even if the list doesn't have it yet.
            for extra in (getattr(self.instance, field, ''), self.data.get(self.add_prefix(field), '') if self.is_bound else ''):
                if extra and extra != ADD_NEW and extra not in values:
                    values.append(extra)
            self.fields[field].widget = forms.Select(
                choices=[('', '—')] + [(v, v) for v in values] + [(ADD_NEW, '+ Add new…')],
                attrs={'class': 'tag-select', 'data-column': column},
            )

    def clean(self):
        data = super().clean()
        for field in Pin.TAG_FIELDS:
            value = (data.get(field) or '').strip()
            data[field] = '' if value == ADD_NEW else value
        return data


class BasePinFormSet(forms.BaseInlineFormSet):
    def __init__(self, *args, **kwargs):
        self._signals = list(Signal.objects.values_list('name', flat=True))
        super().__init__(*args, **kwargs)
        self._tag_options = TagOption.names_for(self.instance)  # this connector's own lists

    def get_form_kwargs(self, index):
        return {**super().get_form_kwargs(index), 'signals': self._signals, 'tag_options': getattr(self, '_tag_options', None)}

    def clean(self):
        super().clean()
        # One type per set: pins with the same set number are one cable set.
        types = {}
        for form in self.forms:
            data = getattr(form, 'cleaned_data', None) or {}
            if data.get('DELETE') or not data.get('set_number'):
                continue
            number, kind = data['set_number'], data.get('set_type') or ''
            if number in types and types[number] != kind:
                form.add_error('set_type', f'Set {number} is already {Pin.SetType(types[number]).label.lower() if types[number] else "without a type"}; '
                                           'pins in one set share a type.')
            types.setdefault(number, kind)

    def save_tag_options(self, connector):
        """Values added with "+ Add new…" join the connector's list for that column."""
        for form in self.forms:
            data = getattr(form, 'cleaned_data', None) or {}
            if data.get('DELETE'):
                continue
            for column, field in enumerate(Pin.TAG_FIELDS, start=1):
                if data.get(field):
                    TagOption.objects.get_or_create(connector=connector, column=column, name=data[field])


PinFormSet = forms.inlineformset_factory(
    Connector, Pin, form=PinForm, formset=BasePinFormSet, extra=0, can_delete=True,
)


class SignalForm(forms.ModelForm):
    class Meta:
        model = Signal
        fields = ['name', 'description']
        widgets = {'name': forms.TextInput(attrs={'style': 'width: 12em'}), 'description': forms.TextInput(attrs={'style': 'width: 28em'})}

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if Signal.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(f'There is already a signal {name}.')
        return name
