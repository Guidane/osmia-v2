from django.contrib.auth import get_user_model

from inventory.models import Part
from tasks.models import Task

from .models import Assembly, AssemblyComponent, TaskLink


def load():
    if Assembly.objects.exists():
        return
    parts = {p.part_number: p for p in Part.objects.all()}

    def bom(assembly, *lines):
        for item, qty in lines:
            if isinstance(item, Assembly):
                AssemblyComponent.objects.create(assembly=assembly, child_assembly=item, quantity=qty)
            elif item in parts:
                AssemblyComponent.objects.create(assembly=assembly, part=parts[item], quantity=qty)

    cable = Assembly.objects.create(
        name='Sensor cable', assembly_type=Assembly.Type.HARNESS, status=Assembly.Status.COMPLETED,
        build_instructions='Crimp and solder the D-sub 25 plug and socket, 1:1 pinout.',
    )
    bom(cable, ('DB25-M', 1), ('DB25-F', 1))

    module = Assembly.objects.create(
        name='Load resistor module', assembly_type=Assembly.Type.GENERIC,
        build_instructions='Put thermal paste on both power resistors and screw them to the heatsink.',
    )
    bom(module, ('RES-100R-50W', 2), ('SCR-M3X8', 1), ('NUT-M3', 1))

    rack = Assembly.objects.create(
        name='Power test rack', assembly_type=Assembly.Type.GENERIC, version='2',
        build_instructions='Mount the load modules and the power supply in the rack, then connect the sensor cable.',
        usage_instructions='Check the resistor temperatures at full load before leaving a test running.',
    )
    bom(rack, (module, 6), (cable, 1), ('PSU-24V-150W', 1), ('SCR-M3X8', 12), ('NUT-M3', 12))

    admin = get_user_model().objects.filter(username='admin').first()
    task = Task.objects.filter(title__startswith='Repair returned PDU').first()
    if task:
        TaskLink.objects.create(task=task, assembly=rack)
    elif admin:
        task = Task.objects.create(title=f'Build {rack}', created_by=admin)
        TaskLink.objects.create(task=task, assembly=rack)
