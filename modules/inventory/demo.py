from core.trees import get_or_create_path

from .models import Attribute, Category, Part, PartAttributeValue

CATEGORY_ATTRIBUTES = {
    'Hardware > Fasteners': [('material', 'stainless steel'), ('thread', 'M3'), ('manufacturer', 'Würth')],
    'Passives > Resistors': [('resistance', '10 kΩ'), ('power', '0.25 W'), ('tolerance', '1%'), ('manufacturer', 'Vishay')],
    'Passives > Capacitors': [('capacitance', '100 µF'), ('voltage', '50 V'), ('type', 'electrolytic')],
    'Power > Power supplies': [('output voltage', '24 V'), ('output power', '150 W'), ('input', '100-240 VAC'),
                               ('manufacturer', 'Mean Well')],
    'Power > Fuses': [('current', '5 A'), ('size', '5x20 mm'), ('speed', 'slow-blow')],
    'Electrical > Connectors': [('pins', '25'), ('manufacturer', 'Amphenol')],
    'Electrical > Contacts': [('wire size', '24-20 AWG'), ('plating', 'gold')],
    'Safety': [('cord length', '1.8 m')],
}

# (part number, description, category, unit, attributes); stock is in the Stock module's demo.
PARTS = [
    ('SCR-M3X8', 'M3 x 8 pan head screw', 'Hardware > Fasteners', 'pcs', {'material': 'stainless steel', 'thread': 'M3'}),
    ('NUT-M3', 'M3 hex nut', 'Hardware > Fasteners', 'pcs', {'material': 'stainless steel', 'thread': 'M3'}),
    ('RES-100R-50W', 'Power resistor 100 Ω 50 W, aluminium housed', 'Passives > Resistors', 'pcs',
     {'resistance': '100 Ω', 'power': '50 W', 'tolerance': '5%', 'manufacturer': 'Arcol'}),
    ('RES-0R1-5W', 'Shunt resistor 0.1 Ω 5 W', 'Passives > Resistors', 'pcs',
     {'resistance': '0.1 Ω', 'power': '5 W', 'tolerance': '1%', 'manufacturer': 'Vishay'}),
    ('RES-10K-0603', 'Resistor 10 kΩ 0603', 'Passives > Resistors', 'pcs',
     {'resistance': '10 kΩ', 'power': '0.1 W', 'tolerance': '1%', 'manufacturer': 'Yageo'}),
    ('CAP-470U-63V', 'Electrolytic capacitor 470 µF 63 V', 'Passives > Capacitors', 'pcs',
     {'capacitance': '470 µF', 'voltage': '63 V', 'type': 'electrolytic'}),
    ('PSU-24V-150W', 'DIN rail power supply 24 V 150 W', 'Power > Power supplies', 'pcs',
     {'output voltage': '24 V', 'output power': '150 W', 'input': '100-240 VAC', 'manufacturer': 'Mean Well'}),
    ('PSU-12V-50W', 'Enclosed power supply 12 V 50 W', 'Power > Power supplies', 'pcs',
     {'output voltage': '12 V', 'output power': '50 W', 'input': '100-240 VAC', 'manufacturer': 'Mean Well'}),
    ('PSU-PROG-3K', 'Programmable DC power supply 0-80 V 3 kW', 'Power > Power supplies', 'pcs',
     {'output voltage': '0-80 V', 'output power': '3 kW', 'input': '3 x 400 VAC'}),
    ('FUSE-T5A', 'Fuse 5 A slow-blow 5x20 mm', 'Power > Fuses', 'pcs', {'current': '5 A', 'size': '5x20 mm', 'speed': 'slow-blow'}),
    ('DB25-M', 'D-sub 25 plug', 'Electrical > Connectors', 'pcs', {'pins': '25'}),
    ('DB25-F', 'D-sub 25 socket', 'Electrical > Connectors', 'pcs', {'pins': '25'}),
    ('DS-PIN-C', 'D-sub crimp contact, pin', 'Electrical > Contacts', 'pcs', {'wire size': '24-20 AWG'}),
    ('DS-SKT-C', 'D-sub crimp contact, socket', 'Electrical > Contacts', 'pcs', {'wire size': '24-20 AWG'}),
    ('ESD-STRAP', 'ESD wrist strap', 'Safety', 'pcs', {'cord length': '1.8 m'}),
]


def load():
    """Additive: creates what's missing and fills blanks on existing demo parts."""
    for path, attrs in CATEGORY_ATTRIBUTES.items():
        category = get_or_create_path(Category, path)
        for name, typical in attrs:
            Attribute.objects.get_or_create(category=category, name=name, defaults={'default_value': typical})

    for number, name, cat, unit, values in PARTS:
        category = get_or_create_path(Category, cat)
        part, created = Part.objects.get_or_create(part_number=number, defaults={'name': name, 'category': category, 'unit': unit})
        if not created and (part.category is None or part.category.name == category.name):
            part.category = category
            part.save()
        for attr_name, value in values.items():
            PartAttributeValue.objects.get_or_create(
                part=part, attribute=category.attributes.get(name=attr_name), defaults={'value': value},
            )

    # How they go together: the plug mates with the socket; each housing takes its crimp contacts,
    # which are crimped with the D-sub crimp tool.
    parts = {p.part_number: p for p in Part.objects.filter(part_number__in=[row[0] for row in PARTS])}
    if 'DB25-M' in parts and 'DB25-F' in parts:
        parts['DB25-M'].mates_with.add(parts['DB25-F'])
    for housing, contact in (('DB25-M', 'DS-PIN-C'), ('DB25-F', 'DS-SKT-C')):
        if housing in parts and contact in parts:
            parts[housing].fits.add(parts[contact])
    from django.apps import apps
    if apps.is_installed('tools'):
        from tools.models import Tool
        crimp = Tool.objects.filter(name='D-sub crimp tool').first()
        insertion = Tool.objects.filter(name='D-sub insertion/extraction tool').first()
        for number in ('DS-PIN-C', 'DS-SKT-C'):
            if number in parts:
                parts[number].tools.add(*[t for t in (crimp, insertion) if t])
