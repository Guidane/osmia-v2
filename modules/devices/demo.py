from django.contrib.auth import get_user_model

from assemblies.models import Assembly, AssemblyComponent
from inventory.models import Part

from .models import Connector, Device, Pin, Signal, TagOption


def _pins(connector, rows):
    """rows: (label, signal[, tag 1[, set number, set type]])."""
    for i, (label, signal, *more) in enumerate(rows, start=1):
        tag = more[0] if more else ''
        set_number, set_type = (more[1], more[2]) if len(more) > 2 else (None, '')
        Pin.objects.create(connector=connector, position=i, label=label, signal=signal, tag1=tag,
                           set_number=set_number, set_type=set_type)


def _signals():
    """The shared signal list gets every signal the demo pins use."""
    for name in sorted(set(Pin.objects.exclude(signal='').values_list('signal', flat=True))):
        Signal.objects.get_or_create(name=name)
    for connector in Connector.objects.all():
        TagOption.from_pins(connector)  # each connector's own tag lists


def _network_demo():
    """A device showing pin tags in two columns and twisted-pair sets: added
    when missing, so older demo databases get it too."""
    if Device.objects.filter(part_number='NET-04').exists():
        return
    switch = Device.objects.create(
        name='Test network switch', part_number='NET-04', origin=Device.Origin.EXTERNAL, role=Device.Role.OTHER,
        manufacturer='Generic', model_number='SW-4P', color='#1f7a8c',
        notes='Demo: Ethernet ports with the pairs as sets, tags in two columns.',
    )
    for n in (1, 2):
        port = Connector.objects.create(device=switch, designator=f'J{n:02d}', side='right' if n == 1 else 'left', position=n,
                                        tag_columns=2,
                                        description=f'Ethernet port {n}')
        rows = [  # label, signal, tag 1, tag 2, set, set type
            ('1', 'TxP', f'ETH{n}_TX+', 'Pair 2', 1, 'twisted'), ('2', 'TxN', f'ETH{n}_TX-', 'Pair 2', 1, 'twisted'),
            ('3', 'RxP', f'ETH{n}_RX+', 'Pair 3', 2, 'twisted'), ('6', 'RxN', f'ETH{n}_RX-', 'Pair 3', 2, 'twisted'),
            ('4', 'NC', '', 'Pair 1', None, ''), ('5', 'NC', '', 'Pair 1', None, ''),
            ('7', 'NC', '', 'Pair 4', None, ''), ('8', 'NC', '', 'Pair 4', None, ''),
            ('S', 'SHIELD', f'ETH{n}_SHIELD', '', None, ''),
        ]
        for i, (label, signal, t1, t2, set_number, set_type) in enumerate(rows, start=1):
            Pin.objects.create(connector=port, position=i, label=label, signal=signal, tag1=t1, tag2=t2,
                               set_number=set_number, set_type=set_type)
    switch.snapshot()


def load():
    if Device.objects.exists():
        _network_demo()
        _signals()
        return
    parts = {p.part_number: p for p in Part.objects.all()}
    users = {u.username: u for u in get_user_model().objects.all()}
    carla, bob = users.get('carla'), users.get('bob')

    # A unit we build: its assembly holds the BOM, the device its connectors.
    pdu_assembly = Assembly.objects.create(
        name='Power distribution unit', assembly_type=Assembly.Type.DEVICE,
        build_instructions='Fit both D-sub 25 sockets to the front panel and wire per the pinout.',
    )
    for number in ('DB25-F', 'SCR-M3X8', 'NUT-M3'):
        if number in parts:
            AssemblyComponent.objects.create(assembly=pdu_assembly, part=parts[number], quantity=2 if number == 'DB25-F' else 4)
    pdu = Device.objects.create(
        name='Power distribution unit', part_number='PDU-100', assembly=pdu_assembly,
        role=Device.Role.PRODUCT, color='#2e8b57', responsible=carla,
    )
    j01 = Connector.objects.create(device=pdu, designator='J01', side='left', position=1,
                                   part=parts.get('DB25-F'), description='Power in')
    _pins(j01, [('1', 'PWR', 'PWR_IN+'), ('13', 'GND', 'PWR_IN-'), ('2', 'PWR', 'PWR_AUX+'), ('14', 'GND', 'PWR_AUX-')])
    j02 = Connector.objects.create(device=pdu, designator='J02', side='right', position=2,
                                   part=parts.get('DB25-F'), description='Control bus')
    _pins(j02, [('1', 'CAN_H', 'CAN1_H', 1, 'twisted_shielded'), ('2', 'CAN_L', 'CAN1_L', 1, 'twisted_shielded'),
                ('3', 'GND', 'CAN1_GND')])
    pdu.snapshot()

    # Test equipment we build to test the units.
    tester = Device.objects.create(
        name='PDU test controller', part_number='TC-01', role=Device.Role.TEST_EQUIPMENT, color='#8a4fbf', responsible=bob,
    )
    p01 = Connector.objects.create(device=tester, designator='J01', side='left', position=1,
                                   part=parts.get('DB25-M'), description='To unit under test')
    _pins(p01, [('1', 'CAN_H'), ('2', 'CAN_L'), ('3', 'GND')])
    tester.snapshot()

    # External equipment.
    psu = Device.objects.create(
        name='Bench power supply', origin=Device.Origin.EXTERNAL, role=Device.Role.POWER_SUPPLY,
        manufacturer='Generic', model_number='PSU-3005', asset_tag='LAB-0042', color='#d9822b',
    )
    out = Connector.objects.create(device=psu, designator='J01', side='right', position=1, description='Output terminals')
    _pins(out, [('+', 'PWR'), ('-', 'GND')])
    psu.snapshot()

    load_dev = Device.objects.create(
        name='Electronic load', origin=Device.Origin.EXTERNAL, role=Device.Role.ELECTRONIC_LOAD,
        manufacturer='Generic', model_number='EL-150', asset_tag='LAB-0057', color='#c0392b',
    )
    inp = Connector.objects.create(device=load_dev, designator='J01', side='left', position=1, description='Load input')
    _pins(inp, [('+', 'PWR'), ('-', 'GND')])
    load_dev.snapshot()
    _network_demo()
    _signals()
