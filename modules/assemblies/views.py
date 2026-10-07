from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView

from core import hooks
from tasks.models import Task

from .forms import AssemblyForm, ComponentFormSet, LinkTaskForm, allowed_sub_assemblies
from .models import Assembly, AssemblyComponent, TaskLink


class AssemblyListView(LoginRequiredMixin, ListView):
    model = Assembly

    def get_queryset(self):
        qs = Assembly.objects.prefetch_related('images').annotate(component_count=Count('components'))
        g = self.request.GET
        if g.get('q'):
            qs = qs.filter(Q(name__icontains=g['q']) | Q(version__icontains=g['q']))
        if g.get('status') in Assembly.Status.values:
            qs = qs.filter(status=g['status'])
        if g.get('type') in Assembly.Type.values:
            qs = qs.filter(assembly_type=g['type'])
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, statuses=Assembly.Status.choices, types=Assembly.Type.choices)


class AssemblyDetailView(LoginRequiredMixin, DetailView):
    model = Assembly

    def get_context_data(self, **kwargs):
        a = self.object
        totals = a.total_parts()
        return super().get_context_data(
            **kwargs,
            components=a.components.select_related('part', 'child_assembly'),
            structure=a.structure(),
            totals=totals,
            buildable=a.buildable_count(totals),
            used_in=a.used_in.select_related('assembly'),
            tasks=Task.objects.filter(assembly_link__assembly=a).select_related('assembly_link', 'assignee'),
            panels=hooks.collect('assembly_detail_panels', self.request, a),
        )


@login_required
def assembly_form(request, pk=None):
    assembly = get_object_or_404(Assembly, pk=pk) if pk else Assembly()
    # Taken before binding: ModelForm validation copies posted values onto the instance.
    before = (assembly.bom_signature(), assembly.version) if pk else None
    sub_assemblies = list(allowed_sub_assemblies(assembly))
    kwargs = {'allowed_assemblies': [a.pk for a in sub_assemblies]}
    form = AssemblyForm(request.POST or None, instance=assembly)
    if request.method == 'POST':
        # No initial here: rows matching their initial value would count as
        # "unchanged" and be skipped, silently dropping them from the BOM.
        formset = ComponentFormSet(request.POST, prefix='components', form_kwargs=kwargs)
    else:
        initial = [{'component': c.ref, 'quantity': c.quantity} for c in assembly.components.all()] if pk else []
        formset = ComponentFormSet(initial=initial, prefix='components', form_kwargs=kwargs)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            assembly = form.save()
            assembly.components.all().delete()
            for ref, qty in formset.lines():
                kind, _, obj_id = ref.partition(':')
                field = 'part_id' if kind == 'p' else 'child_assembly_id'
                AssemblyComponent.objects.create(assembly=assembly, quantity=qty, **{field: int(obj_id)})
            if before is not None and before != (assembly.bom_signature(), assembly.version):
                assembly.revision += 1
                assembly.save(update_fields=['revision'])
                messages.info(request, f'Components or version changed: now revision {assembly.revision}.')
        messages.success(request, 'Assembly saved.')
        return redirect(assembly)
    return render(request, 'assemblies/assembly_form.html', {
        'form': form, 'formset': formset, 'assembly': assembly, 'sub_assemblies': sub_assemblies,
        'heading': f'Edit {assembly}' if pk else 'New assembly',
    })


@login_required
@require_POST
def create_build_task(request, pk):
    assembly = get_object_or_404(Assembly, pk=pk)
    with transaction.atomic():
        task = Task.objects.create(
            title=f'Build {assembly}', created_by=request.user, assignee=request.user,
            description=assembly.build_instructions,
        )
        TaskLink.objects.create(task=task, assembly=assembly)
    messages.success(request, 'Build task created. Set its dates and assignee.')
    return redirect('tasks:edit', task.pk)


@login_required
@require_POST
def link_task(request, task_pk):
    """Set or clear the assembly a task is for (from the task page's panel)."""
    task = get_object_or_404(Task, pk=task_pk)
    form = LinkTaskForm(request.POST)
    if form.is_valid():
        if form.cleaned_data['assembly']:
            TaskLink.objects.update_or_create(task=task, defaults={'assembly': form.cleaned_data['assembly']})
        else:
            TaskLink.objects.filter(task=task).delete()
    return redirect(task)
