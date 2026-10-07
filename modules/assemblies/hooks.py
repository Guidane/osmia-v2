from django.urls import reverse

from core import hooks

from .forms import LinkTaskForm
from .models import Assembly, AssemblyComponent, TaskLink


@hooks.register('dashboard_widgets')
def in_manufacturing(request):
    count = Assembly.objects.filter(status=Assembly.Status.MANUFACTURING).count()
    return hooks.Widget('Assemblies in manufacturing', count, reverse('assemblies:list') + '?status=manufacturing')


@hooks.register('task_detail_panels')
def task_assembly(request, task):
    link = TaskLink.objects.filter(task=task).select_related('assembly').first()
    assembly = link.assembly if link else None
    totals = assembly.total_parts() if assembly else []
    return hooks.panel('Assembly', 'assemblies/_task_panel.html', {
        'task': task,
        'assembly': assembly,
        'totals': totals,
        'short': [r for r in totals if r['short']],
        'form': LinkTaskForm(initial={'assembly': assembly}),
    }, request, order=5)


@hooks.register('part_detail_panels')
def part_used_in(request, part):
    uses = AssemblyComponent.objects.filter(part=part).select_related('assembly')
    return hooks.panel('Used in assemblies', 'assemblies/_part_panel.html', {'uses': uses}, request)


# -- Automations: assembly status ------------------------------------------------------

from django.db.models.signals import post_save
from django.dispatch import receiver

from core import automation
from core.automation import Field

ASSEMBLY_FIELDS = [
    Field('name', 'Name', lambda a: a.name),
    Field('status', 'Status', lambda a: a.get_status_display()),
]
automation.event('assemblies.status_changed', "An assembly's status changes", fields=ASSEMBLY_FIELDS)
automation.event('assemblies.completed', 'An assembly is completed', fields=ASSEMBLY_FIELDS)
automation.status_model(Assembly)
automation.track(Assembly, 'status')


@receiver(post_save, sender=Assembly, dispatch_uid='assemblies-automation-events')
def assembly_saved(sender, instance, created, **kwargs):
    change = automation.changed(instance, 'status')
    automation.remember_saved(instance, 'status')
    if created or not change:
        return
    automation.emit('assemblies.status_changed', instance)
    if instance.status == Assembly.Status.COMPLETED:
        automation.emit('assemblies.completed', instance)
