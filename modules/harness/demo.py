from .models import SignalRule


def load():
    # The stand-alone designer's defaults.
    if not SignalRule.objects.exists():
        SignalRule.replace_all([['PWR', 'PWR'], ['GND', 'GND'], ['RX', 'TX'], ['CAN_H', 'CAN_H'], ['CAN_L', 'CAN_L']])
