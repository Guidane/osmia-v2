from django.db import migrations

# A starter list of common signals; ones that already exist (any case) are left alone.
BASIC_SIGNALS = [
    ('GND', 'Ground'), ('PWR', 'Supply voltage'), ('0V', 'Supply return'), ('+5V', '5 V supply'), ('+12V', '12 V supply'),
    ('+24V', '24 V supply'), ('VBAT', 'Battery'), ('PE', 'Protective earth'), ('SHIELD', 'Cable shield / drain'),
    ('TxP', 'Transmit +, differential pair'), ('TxN', 'Transmit −, differential pair'),
    ('RxP', 'Receive +, differential pair'), ('RxN', 'Receive −, differential pair'),
    ('TX', 'Transmit, single-ended (e.g. UART)'), ('RX', 'Receive, single-ended (e.g. UART)'),
    ('CAN_H', 'CAN bus high'), ('CAN_L', 'CAN bus low'), ('RS485_A', 'RS-485 A (−)'), ('RS485_B', 'RS-485 B (+)'),
    ('SDA', 'I²C data'), ('SCL', 'I²C clock'), ('USB_D+', 'USB data +'), ('USB_D-', 'USB data −'),
    ('AIN', 'Analog input'), ('AOUT', 'Analog output'), ('DIN', 'Digital input'), ('DOUT', 'Digital output'),
    ('NC', 'Not connected'),
]


def add_signals(apps, schema_editor):
    Signal = apps.get_model('devices', 'Signal')
    have = {n.upper() for n in Signal.objects.values_list('name', flat=True)}
    Signal.objects.bulk_create([Signal(name=n, description=d) for n, d in BASIC_SIGNALS if n.upper() not in have])


class Migration(migrations.Migration):

    dependencies = [
        ('devices', '0003_initial'),
    ]

    operations = [
        migrations.RunPython(add_signals, migrations.RunPython.noop),
    ]
