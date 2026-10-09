from users.models import User

from .models import HarnessProject, SignalRule


def load():
    # The stand-alone designer's defaults.
    if not SignalRule.objects.exists():
        SignalRule.replace_all([['PWR', 'PWR'], ['GND', 'GND'], ['RX', 'TX'], ['CAN_H', 'CAN_H'], ['CAN_L', 'CAN_L']])
    # A project to open in the designer (empty: place the devices from the sidebar).
    if not HarnessProject.objects.exists():
        HarnessProject(created_by=User.objects.filter(username='carla').first()).save_version(
            {'name': 'PDU-100 test bench harness'}, User.objects.filter(username='carla').first())
