from .models import Tool

TOOLS = [
    # name, part number, kind, manufacturer, kept at, notes
    ('D-sub crimp tool', '58448-2', Tool.Kind.CRIMP, 'TE Connectivity', 'Bench 2, drawer 1',
     'For D-sub stamped contacts, 24-20 AWG. Use the die marked "DS".'),
    ('D-sub insertion/extraction tool', '91067-2', Tool.Kind.INSERTION, 'TE Connectivity', 'Bench 2, drawer 1', ''),
    ('Wire stripper 0.2-6 mm²', 'WS-6', Tool.Kind.STRIPPER, 'Generic', 'Bench 1', ''),
]


def load():
    for name, number, kind, maker, storage, notes in TOOLS:
        Tool.objects.get_or_create(name=name, defaults={
            'part_number': number, 'kind': kind, 'manufacturer': maker, 'storage': storage, 'notes': notes,
        })
