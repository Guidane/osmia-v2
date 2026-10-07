from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Count, Max
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import DeleteView, DetailView, ListView

from .forms import RuleForm, catalogue, describe
from .models import Notification, Rule, Run


class RuleListView(LoginRequiredMixin, ListView):
    model = Rule

    def get_queryset(self):
        return Rule.objects.annotate(run_count=Count('runs'), last_run=Max('runs__created_at'))


class RuleDetailView(LoginRequiredMixin, DetailView):
    model = Rule

    def get_context_data(self, **kwargs):
        conditions, steps = describe(self.object)
        return super().get_context_data(
            **kwargs, conditions=conditions, steps=steps, runs=self.object.runs.all()[:25],
        )


@login_required
def rule_form(request, pk=None):
    rule = get_object_or_404(Rule, pk=pk) if pk else Rule()
    if not request.user.has_perm('automations.change_rule' if pk else 'automations.add_rule'):
        messages.error(request, "You don't have permission to edit automation rules.")
        return redirect(rule if pk else 'automations:list')
    initial = {'trigger': request.GET['trigger']} if request.GET.get('trigger') and not rule.pk else {}
    form = RuleForm(request.POST or None, instance=rule, initial=initial)
    if request.method == 'POST' and form.is_valid():
        if not rule.pk:
            form.instance.created_by = request.user
        rule = form.save()
        messages.success(request, f'Rule "{rule}" saved.')
        return redirect(rule)
    return render(request, 'automations/rule_form.html', {
        'form': form, 'rule': rule, 'catalogue': catalogue(rule),
        'heading': f'Edit {rule}' if rule.pk else 'New rule',
    })


@login_required
@permission_required('automations.change_rule', raise_exception=True)
@require_POST
def toggle(request, pk):
    rule = get_object_or_404(Rule, pk=pk)
    rule.active = not rule.active
    rule.save(update_fields=['active', 'updated_at'])
    messages.success(request, f'Rule "{rule}" is {"on" if rule.active else "off"}.')
    return redirect(rule)


class RuleDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = Rule
    permission_required = 'automations.delete_rule'
    template_name = 'core/confirm_delete.html'
    success_url = reverse_lazy('automations:list')


class RunListView(LoginRequiredMixin, ListView):
    model = Run
    template_name = 'automations/run_list.html'

    def get_queryset(self):
        qs = Run.objects.select_related('rule')
        if self.request.GET.get('status') in Run.Status.values:
            qs = qs.filter(status=self.request.GET['status'])
        return qs[:200]


@login_required
def notifications(request):
    if request.method == 'POST':
        request.user.notifications.filter(read=False).update(read=True)
        return redirect('automations:notifications')
    return render(request, 'automations/notification_list.html', {
        'notifications': request.user.notifications.select_related('rule')[:100],
    })


@login_required
def open_notification(request, pk):
    note = get_object_or_404(Notification, pk=pk, user=request.user)
    if not note.read:
        note.read = True
        note.save(update_fields=['read'])
    if note.url and url_has_allowed_host_and_scheme(note.url, allowed_hosts={request.get_host()}):
        return redirect(note.url)
    return redirect('automations:notifications')
