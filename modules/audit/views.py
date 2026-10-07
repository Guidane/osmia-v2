from collections import OrderedDict
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, F, Max, Min, Q
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import ListView, TemplateView

from core.modules import get_module, installed_modules

from .models import HiddenHistory, LogEntry


def page_query(request):
    """The current filters, ready to put in front of "page=" in a link."""
    q = request.GET.copy()
    q.pop('page', None)
    return q.urlencode() + '&' if q else ''


def cross_module(qs):
    """Entries made by another module's code: the ones with a chain tag."""
    return qs.exclude(actor_module='').exclude(actor_module=F('module'))


class LogListView(LoginRequiredMixin, ListView):
    """All changes, or one module's (?module=tasks)."""
    model = LogEntry
    paginate_by = 100
    template_name = 'audit/log_list.html'

    def get_queryset(self):
        qs = LogEntry.objects.select_related('user')
        g = self.request.GET
        if g.get('module'):
            qs = qs.filter(module=g['module'])
        if g.get('action') in LogEntry.Action.values:
            qs = qs.filter(action=g['action'])
        if g.get('user'):
            qs = qs.filter(user_id=g['user']) if g['user'].isdigit() else qs.filter(user__isnull=True)
        if g.get('chained'):
            qs = cross_module(qs)
        for word in g.get('q', '').split():
            qs = qs.filter(Q(object_label__icontains=word) | Q(item_label__icontains=word) | Q(chain_id=word)
                           | Q(source__icontains=word))
        return qs

    def get_context_data(self, **kwargs):
        module = get_module(self.request.GET.get('module', ''))
        return super().get_context_data(
            **kwargs,
            module=module,
            modules=[m for m in installed_modules() if m.label != 'audit'],
            actions=LogEntry.Action.choices,
            people=get_user_model().objects.filter(is_active=True),
            page_query=page_query(self.request),
        )


class ChainListView(LoginRequiredMixin, TemplateView):
    """Chains of changes that crossed modules, newest first."""
    template_name = 'audit/chain_list.html'

    def get_context_data(self, **kwargs):
        ids = cross_module(LogEntry.objects.exclude(chain_id='')).values_list('chain_id', flat=True).distinct()
        chains = list(
            LogEntry.objects.filter(chain_id__in=ids).values('chain_id')
            .annotate(count=Count('id'), started=Min('created_at'), ended=Max('created_at'))
            .order_by('-started')[:200]
        )
        entries = LogEntry.objects.filter(chain_id__in=[c['chain_id'] for c in chains]).select_related('user').order_by('id')
        by_chain = {}
        for e in entries:
            by_chain.setdefault(e.chain_id, []).append(e)
        for c in chains:
            items = by_chain.get(c['chain_id'], [])
            first = items[0] if items else None
            c['first'] = first
            c['user'] = first.user if first else None
            c['modules'] = list(OrderedDict.fromkeys(e.module for e in items))
            longest = max((e.chain_modules for e in items), key=len, default=[])
            c['path'] = longest
            c['source'] = next((e.source for e in items if e.source), '')
        return super().get_context_data(**kwargs, chains=chains)


class ChainDetailView(LoginRequiredMixin, TemplateView):
    template_name = 'audit/chain_detail.html'

    def get_context_data(self, **kwargs):
        entries = list(LogEntry.objects.filter(chain_id=self.kwargs['chain_id']).select_related('user').order_by('id'))
        if not entries:
            raise Http404('No such chain.')
        path = max((e.chain_modules for e in entries), key=len)
        return super().get_context_data(
            **kwargs, entries=entries,
            path=path, modules=list(OrderedDict.fromkeys(e.module for e in entries)),
            user=entries[0].user, started=entries[0].created_at, source=next((e.source for e in entries if e.source), ''),
        )


class HistoryView(LoginRequiredMixin, ListView):
    """Everything that happened to one record."""
    template_name = 'audit/history.html'
    paginate_by = 100

    def get_queryset(self):
        self.content_type = get_object_or_404(ContentType, pk=self.kwargs['ct'])
        return LogEntry.objects.filter(content_type=self.content_type, object_id=self.kwargs['pk']).select_related('user')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        first = self.get_queryset().first()
        record = None
        try:
            record = self.content_type.get_object_for_this_type(pk=self.kwargs['pk'])
        except Exception:
            pass
        ctx.update(page_query=page_query(self.request), record=record, label=str(record) if record else (first.object_label if first else 'Deleted record'),
                   record_url=getattr(record, 'get_absolute_url', lambda: '')() if record else '')
        return ctx


class ActivityView(LoginRequiredMixin, TemplateView):
    """Who changed how much, recently."""
    template_name = 'audit/activity.html'

    def get_context_data(self, **kwargs):
        now = timezone.now()
        day, week, month = now - timedelta(days=1), now - timedelta(days=7), now - timedelta(days=30)
        rows = (LogEntry.objects.values('user_id')
                .annotate(today=Count('id', filter=Q(created_at__gte=day)),
                          week=Count('id', filter=Q(created_at__gte=week)),
                          month=Count('id', filter=Q(created_at__gte=month)),
                          total=Count('id'), last=Max('created_at'))
                .order_by('-last'))
        users = get_user_model().objects.in_bulk([r['user_id'] for r in rows if r['user_id']])
        for r in rows:
            r['person'] = users.get(r['user_id'])
        by_module = (LogEntry.objects.filter(created_at__gte=month).values('module')
                     .annotate(count=Count('id'), chained=Count('id', filter=~Q(actor_module='') & ~Q(actor_module=F('module'))))
                     .order_by('-count'))
        return super().get_context_data(**kwargs, rows=rows, by_module=by_module)


class SettingsView(LoginRequiredMixin, TemplateView):
    """Which modules show their history (the History panel and the Log link).
    Hiding it only changes the pages: the changes are logged all the same."""
    template_name = 'audit/settings.html'

    def modules(self):
        return [m for m in installed_modules() if m.label != 'audit']

    def get_context_data(self, **kwargs):
        hidden = set(HiddenHistory.objects.values_list('module', flat=True))
        rows = [{'module': m, 'shown': m.label not in hidden} for m in self.modules()]
        return super().get_context_data(**kwargs, rows=rows, can_change=self.request.user.is_superuser)

    def post(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        shown = set(request.POST.getlist('shown'))
        labels = {m.label for m in self.modules()}
        HiddenHistory.objects.filter(module__in=labels & shown).delete()
        for label in labels - shown:
            HiddenHistory.objects.get_or_create(module=label)
        messages.success(request, 'Saved. Every change is still logged here in Audit.')
        return redirect('audit:settings')
