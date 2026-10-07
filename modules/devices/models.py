"""Electrical devices and their named connectors.

A device is anything with connectors that takes part in an electrical setup:
units the company builds (a computer, an inverter, ...), the test equipment
it builds to test them, and external equipment such as power supplies and
electronic loads. Each connector (J01, P01, ...) has numbered pins with a
label and a signal.

The definition (name, connectors, pins) is versioned the same way as the
harness designer's device library: every change is kept as an immutable
snapshot and ``version`` points at the current one.
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.db import models, transaction
from django.urls import reverse


class Device(models.Model):
    class Origin(models.TextChoices):
        IN_HOUSE = 'in_house', 'Made by us'
        EXTERNAL = 'external', 'External'

    class Role(models.TextChoices):
        PRODUCT = 'product', 'Product (unit we build)'
        TEST_EQUIPMENT = 'test_equipment', 'Test equipment'
        POWER_SUPPLY = 'power_supply', 'Power supply'
        ELECTRONIC_LOAD = 'electronic_load', 'Electronic / test load'
        MEASUREMENT = 'measurement', 'Measurement (DMM, scope, ...)'
        SIGNAL_SOURCE = 'signal_source', 'Signal generator / source'
        INTERCONNECT = 'interconnect', 'Interconnect (adapter, breakout, extension)'
        OTHER = 'other', 'Other'

    name = models.CharField(max_length=200)
    part_number = models.CharField(max_length=100, blank=True)
    images = GenericRelation('core.Image')  # pictures, shown with {% image_gallery %}
    origin = models.CharField(max_length=20, choices=Origin, default=Origin.IN_HOUSE)
    role = models.CharField(max_length=20, choices=Role, default=Role.PRODUCT)
    # For devices we build: the assembly (bill of materials) behind it.
    assembly = models.OneToOneField(
        'assemblies.Assembly', null=True, blank=True, on_delete=models.SET_NULL, related_name='device',
        limit_choices_to={'assembly_type': 'device'},
    )
    manufacturer = models.CharField(max_length=100, blank=True)
    model_number = models.CharField(max_length=100, blank=True)
    asset_tag = models.CharField(max_length=100, blank=True)
    color = models.CharField(max_length=7, default='#3b7dd8', help_text='Colour of the device in the harness designer.')
    responsible = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='devices',
        help_text='Owner; verifies wiring to this device in harnesses.',
    )
    notes = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.part_number})' if self.part_number else self.name

    def get_absolute_url(self):
        return reverse('devices:detail', args=[self.pk])

    @property
    def is_external(self):
        return self.origin == self.Origin.EXTERNAL

    @property
    def is_interconnect(self):
        """Passes signals through: its input pins are mapped to output pins, and
        in harnesses the mapped pins take the tags of the unit on the other side."""
        return self.role == self.Role.INTERCONNECT

    # -- harness-format definition ------------------------------------------

    def definition(self):
        """The device in the harness designer's JSON format (without id/version)."""
        connectors = list(self.connectors.prefetch_related('pins'))
        data = {
            'name': self.name,
            'part_number': self.part_number,
            'color': self.color,
            'role': self.role,
            'responsible_user_id': str(self.responsible_id) if self.responsible_id else None,
            'connectors': [
                {
                    'id': c.designator,
                    'side': c.side,
                    'tag_columns': c.tag_column_count(),
                    'pins': [
                        {'id': str(p.position), 'label': p.label, 'signal': p.signal, 'tags': p.tags,
                         'set': p.set_number, 'set_type': p.set_type}
                        for p in c.pins.all()
                    ],
                }
                for c in connectors
            ],
        }
        if self.is_interconnect:
            # [[connector, pin, connector, pin], ...] with pin ids as in 'pins' above
            data['pin_map'] = [
                [m.from_pin.connector.designator, str(m.from_pin.position), m.to_pin.connector.designator, str(m.to_pin.position)]
                for m in self.pin_maps.select_related('from_pin__connector', 'to_pin__connector')
            ]
        return data

    @transaction.atomic
    def apply_definition(self, data):
        """Replace name/colour/owner and all connectors and pins from a
        harness-format dict. Connector part links are kept by designator."""
        self.name = (data.get('name') or self.name or 'Unnamed device').strip()
        self.part_number = (data.get('part_number') or '').strip()
        self.color = data.get('color') or self.color
        if data.get('role') in self.Role.values:
            self.role = data['role']
        owner = data.get('responsible_user_id')
        self.responsible_id = int(owner) if str(owner or '').isdigit() else None
        self.save()
        parts = {c.designator: (c.part_id, c.description) for c in self.connectors.all()}
        self.connectors.all().delete()
        for c_index, c in enumerate(data.get('connectors') or []):
            designator = (c.get('id') or f'J{c_index + 1:02d}').strip()
            part_id, description = parts.get(designator, (None, ''))
            connector = Connector.objects.create(
                device=self, designator=designator, position=c_index,
                side=c.get('side') if c.get('side') in Connector.Side.values else Connector.Side.RIGHT,
                tag_columns=min(4, max(1, int(c.get('tag_columns') or 1))) if str(c.get('tag_columns') or '1').isdigit() else 1,
                part_id=part_id, description=description,
            )
            Pin.objects.bulk_create(
                Pin(
                    connector=connector, position=p_index + 1,
                    label=(p.get('label') or str(p_index + 1)).strip()[:20],
                    signal=(p.get('signal') or '').strip()[:50],
                    **Pin.tags_from(p),
                    set_number=p.get('set') if isinstance(p.get('set'), int) else None,
                    set_type=p.get('set_type') if p.get('set_type') in Pin.SetType.values else '',
                )
                for p_index, p in enumerate(c.get('pins') or [])
            )
        for connector in self.connectors.all():
            TagOption.from_pins(connector)
        pins = {(p.connector.designator, str(p.position)): p for p in Pin.objects.filter(connector__device=self).select_related('connector')}
        for row in data.get('pin_map') or []:
            if len(row) == 4 and (row[0], row[1]) in pins and (row[2], row[3]) in pins:
                PinMap.objects.get_or_create(device=self, from_pin=pins[(row[0], row[1])], to_pin=pins[(row[2], row[3])])

    def snapshot(self, user=None):
        """Record the current definition as a new version if it changed.
        Returns True when a new version was created."""
        data = self.definition()
        current = self.versions.filter(version=self.version).first()
        if current is not None and current.data == data:
            return False
        last = self.versions.order_by('-version').values_list('version', flat=True).first() or 0
        DeviceVersion.objects.create(device=self, version=last + 1, data=data, created_by=user)
        self.version = last + 1
        self.save(update_fields=['version'])
        return True

    @transaction.atomic
    def activate_version(self, version):
        """Make an older version current again (like moving the harness pointer)."""
        snap = self.versions.get(version=version)
        self.apply_definition(snap.data)
        self.version = version
        self.save(update_fields=['version'])

    def to_harness(self):
        """The definition plus what the designer shows but doesn't version,
        e.g. each connector's part (number, type and a link to its page)."""
        data = {**self.definition(), 'id': str(self.pk), 'version': self.version, 'url': self.get_absolute_url(),
                'origin': self.origin}
        parts = {c.designator: c.part for c in self.connectors.select_related('part')}
        for connector in data['connectors']:
            part = parts.get(connector['id'])
            connector['part'] = {
                'part_number': part.part_number,
                'name': part.name,
                'type': part.name,
                'url': part.get_absolute_url(),
            } if part else None
        return data

    def next_designator(self, prefix='J'):
        taken = set(self.connectors.values_list('designator', flat=True))
        n = 1
        while f'{prefix}{n:02d}' in taken:
            n += 1
        return f'{prefix}{n:02d}'

    @transaction.atomic
    def clone_connector(self, connector, designator=None):
        """A copy of ``connector`` (settings and pins) on this device."""
        copy = Connector.objects.create(
            device=self, designator=designator or self.next_designator(connector.designator[:1] or 'J'),
            side=connector.side, part=connector.part,
            description=connector.description, position=self.connectors.count() + 1, tag_columns=connector.tag_column_count(),
        )
        Pin.objects.bulk_create(
            Pin(connector=copy, position=p.position, label=p.label, signal=p.signal, **p.tag_fields(),
                set_number=p.set_number, set_type=p.set_type)
            for p in connector.pins.all()
        )
        TagOption.objects.bulk_create(TagOption(connector=copy, column=o.column, name=o.name) for o in connector.tag_options.all())
        return copy

    @classmethod
    @transaction.atomic
    def make_extension(cls, device, connector, user=None):
        """An extension for ``device``'s ``connector``: an interconnect whose
        input (J01, the harness from the connector plugs in here) is mapped pin
        for pin to an output (J02) with exactly the same pinout."""
        ext = cls.objects.create(
            name=f'{device.name} {connector.designator} extension'[:200], role=cls.Role.INTERCONNECT,
            origin=device.origin, color='#6b7280', responsible=user,
            notes=f'Extends {device.name} {connector.designator}: J02 has the same pinout.',
        )
        inp = Connector.objects.create(device=ext, designator='J01', side=Connector.Side.LEFT, position=1,
                                       tag_columns=connector.tag_column_count(),
                                       description=f'In, from {device.name} {connector.designator}')
        out = Connector.objects.create(device=ext, designator='J02', side=Connector.Side.RIGHT, position=2,
                                       part=connector.part,
                                       tag_columns=connector.tag_column_count(),
                                       description=f'Out, same pinout as {device.name} {connector.designator}')
        for p in connector.pins.all():
            kw = dict(position=p.position, label=p.label, signal=p.signal, set_number=p.set_number, set_type=p.set_type, **p.tag_fields())
            a = Pin.objects.create(connector=inp, **kw)
            b = Pin.objects.create(connector=out, **kw)
            PinMap.objects.create(device=ext, from_pin=a, to_pin=b)
        for c in (inp, out):
            TagOption.from_pins(c)
        ext.snapshot(user)
        return ext


class Connector(models.Model):
    def audit_record(self):
        return self.device  # logged on the device

    class Side(models.TextChoices):
        LEFT = 'left', 'Left'
        RIGHT = 'right', 'Right'

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name='connectors')
    designator = models.CharField(max_length=20, help_text='e.g. J01 (jack on the device) or P01 (plug).')
    side = models.CharField(max_length=5, choices=Side, default=Side.RIGHT, help_text='Where it sits in the harness designer.')
    part = models.ForeignKey(
        'inventory.Part', null=True, blank=True, on_delete=models.SET_NULL, related_name='device_connectors',
        verbose_name='Physical connector', help_text='The connector part, e.g. a D-sub 25 socket. Its description is shown as the connector type in harnesses.',
    )
    # How many of the pins' tag columns (Tag 1..4) this connector shows; more are added in the pin editor.
    tag_columns = models.PositiveSmallIntegerField(default=1)
    description = models.CharField('Function', max_length=200, blank=True, help_text='What the connector is for, e.g. "Main power in".')
    position = models.PositiveIntegerField(default=0)
    images = GenericRelation('core.Image')  # e.g. a drawing of the pinout

    class Meta:
        ordering = ['position', 'designator']
        constraints = [models.UniqueConstraint(fields=['device', 'designator'], name='unique_connector_designator')]

    def __str__(self):
        return f'{self.device.name} {self.designator}'

    @property
    def mating_designator(self):
        """The harness-side connector name: J01 mates with P01 and vice versa."""
        prefix, rest = self.designator[:1].upper(), self.designator[1:]
        return ('P' if prefix != 'P' else 'J') + rest

    def signal_summary(self):
        signals = [p.signal for p in self.pins.all() if p.signal]
        return ', '.join(sorted(set(signals)))

    MAX_TAG_COLUMNS = 4

    def tag_column_count(self):
        """The tag columns to show: as many as set, and never fewer than the pins use."""
        used = 0
        for pin in self.pins.all():
            for i, value in enumerate(pin.tags, start=1):
                if value:
                    used = max(used, i)
        return max(1, min(self.MAX_TAG_COLUMNS, max(self.tag_columns or 1, used)))

    def tag_column_numbers(self):
        return list(range(1, self.tag_column_count() + 1))

    @property
    def type_label(self):
        """What the connector is: its part's description."""
        return (self.part.name or self.part.part_number) if self.part else ''

    def part_details(self):
        """The connector part's attributes, e.g. [("Positions", "25"), ...]."""
        if not self.part:
            return []
        return [(v.attribute.name, v.value) for v in self.part.attribute_values.select_related('attribute') if v.value]

    def get_absolute_url(self):
        return f'{self.device.get_absolute_url()}?connector={self.pk}'


class Pin(models.Model):
    def audit_record(self):
        return self.connector.device  # logged on the device

    class SetType(models.TextChoices):
        STRAIGHT = 'straight', 'Straight'
        TWISTED = 'twisted', 'Twisted'
        SHIELDED = 'shielded', 'Shielded'
        TWISTED_SHIELDED = 'twisted_shielded', 'Twisted shielded'

    connector = models.ForeignKey(Connector, on_delete=models.CASCADE, related_name='pins')
    position = models.PositiveIntegerField()
    label = models.CharField(max_length=20, help_text='The number printed on the connector, e.g. 13.')
    signal = models.CharField(max_length=50, blank=True, help_text='One of the signals in Devices > Signals.')
    # Four tag columns, each picked from its own list (Devices > Tags).
    tag1 = models.CharField('Tag 1', max_length=50, blank=True)
    tag2 = models.CharField('Tag 2', max_length=50, blank=True)
    tag3 = models.CharField('Tag 3', max_length=50, blank=True)
    tag4 = models.CharField('Tag 4', max_length=50, blank=True)
    # Pins wired as one cable set, e.g. a twisted pair: same set number, and how they're run.
    set_number = models.PositiveSmallIntegerField('Set', null=True, blank=True)
    set_type = models.CharField(max_length=20, choices=SetType, blank=True)

    class Meta:
        ordering = ['position']
        constraints = [models.UniqueConstraint(fields=['connector', 'position'], name='unique_pin_position')]

    TAG_FIELDS = ('tag1', 'tag2', 'tag3', 'tag4')

    @property
    def tags(self):
        return [getattr(self, f) for f in self.TAG_FIELDS]

    def tag_fields(self):
        return {f: getattr(self, f) for f in self.TAG_FIELDS}

    @classmethod
    def tags_from(cls, data):
        """Tag fields from a harness-format pin (older versions had one 'tag')."""
        values = list(data.get('tags') or [data.get('tag') or ''])
        values += [''] * (4 - len(values))
        return {f: str(v or '').strip()[:50] for f, v in zip(cls.TAG_FIELDS, values)}

    def __str__(self):
        return f'{self.connector} pin {self.label}'


class PinMap(models.Model):
    """On an interconnect: an input pin passed through to an output pin."""

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name='pin_maps')
    from_pin = models.ForeignKey(Pin, on_delete=models.CASCADE, related_name='maps_out')
    to_pin = models.ForeignKey(Pin, on_delete=models.CASCADE, related_name='maps_in')

    class Meta:
        ordering = ['from_pin__connector__position', 'from_pin__position']
        constraints = [models.UniqueConstraint(fields=['from_pin', 'to_pin'], name='unique_pin_map')]

    def __str__(self):
        return f'{self.from_pin.connector.designator}.{self.from_pin.label} → {self.to_pin.connector.designator}.{self.to_pin.label}'


class TagOption(models.Model):
    """A value offered in one of a connector's tag columns. Tags are local to
    the connector: each connector has its own lists (signals are global)."""

    connector = models.ForeignKey('Connector', on_delete=models.CASCADE, related_name='tag_options')
    column = models.PositiveSmallIntegerField(choices=[(i, f'Tag {i}') for i in range(1, 5)])
    name = models.CharField(max_length=50)
    audit_log = False  # follows the pins' tags, which are logged

    class Meta:
        ordering = ['column', 'name']
        constraints = [models.UniqueConstraint(fields=['connector', 'column', 'name'], name='unique_tag_option_per_connector')]

    def __str__(self):
        return f'{self.connector} Tag {self.column}: {self.name}'

    @classmethod
    def names_for(cls, connector):
        """{column: [names]} of a connector's four tag columns."""
        out = {i: [] for i in range(1, 5)}
        if connector is not None and connector.pk:
            for column, name in cls.objects.filter(connector=connector).values_list('column', 'name'):
                out[column].append(name)
        return out

    @classmethod
    def from_pins(cls, connector):
        """Make sure every tag the connector's pins use is in its lists."""
        for pin in connector.pins.all():
            for column, value in enumerate(pin.tags, start=1):
                if value:
                    cls.objects.get_or_create(connector=connector, column=column, name=value)


class Signal(models.Model):
    """The signals pins can carry, shared by all devices (Devices > Signals)."""

    name = models.CharField(max_length=50, unique=True)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @transaction.atomic
    def rename(self, new_name):
        """Rename, and the pins (and harness signal rules) that use it follow."""
        old = self.name
        self.name = new_name
        self.save()
        if old != new_name:
            Pin.objects.filter(signal=old).update(signal=new_name)
            from django.apps import apps
            if apps.is_installed('harness'):
                SignalRule = apps.get_model('harness', 'SignalRule')
                SignalRule.objects.filter(signal_a=old).update(signal_a=new_name)
                SignalRule.objects.filter(signal_b=old).update(signal_b=new_name)

    def usage(self):
        return Pin.objects.filter(signal=self.name).count()


class DeviceVersion(models.Model):
    """An immutable snapshot of a device definition, in harness format."""
    audit_log = False  # history/bookkeeping, not an item people change

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name='versions')
    version = models.PositiveIntegerField()
    data = models.JSONField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-version']
        constraints = [models.UniqueConstraint(fields=['device', 'version'], name='unique_device_version')]

    def __str__(self):
        return f'{self.device.name} v{self.version}'
