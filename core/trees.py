"""Shared support for nested records (categories, locations, departments, budgets)."""
import re

from django import forms
from django.db import models


class TreeNode(models.Model):
    """A named node with an optional parent, e.g. ``Warehouse > Aisle 1 > Bin A1``."""

    name = models.CharField(max_length=100)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')

    class Meta:
        abstract = True
        ordering = ['name']

    def __str__(self):
        return self.full_path()

    def ancestors(self):
        node, chain, seen = self, [], set()
        while node is not None and node.pk not in seen:
            seen.add(node.pk)
            chain.append(node)
            node = node.parent
        return list(reversed(chain))

    def full_path(self, sep=' > '):
        return sep.join(n.name for n in self.ancestors())

    def descendant_ids(self):
        ids, frontier = set(), [self.pk]
        while frontier:
            children = type(self).objects.filter(parent_id__in=frontier).values_list('pk', flat=True)
            frontier = [pk for pk in children if pk not in ids]
            ids.update(frontier)
        return ids


class TreeNodeForm(forms.ModelForm):
    """Name + parent, where the parent can't be the node itself or one of its descendants."""

    class Meta:
        fields = ['name', 'parent']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = self._meta.model.objects.select_related('parent')
        if self.instance.pk:
            qs = qs.exclude(pk__in={self.instance.pk, *self.instance.descendant_ids()})
        self.fields['parent'].queryset = qs


def link_parents(nodes):
    """Load ``nodes`` (all of one tree) and connect each to its parent in
    memory, so walking up the tree (ancestors, full_path) needs no queries."""
    nodes = list(nodes)
    by_pk = {n.pk: n for n in nodes}
    for n in nodes:
        if n.parent_id in by_pk:
            n.parent = by_pk[n.parent_id]
    return nodes


def natural_key(text):
    """Sort "Rack 2" before "Rack 10": digits compare as numbers."""
    parts = re.split(r'(\d+)', text.lower())
    return tuple(int(p) if i % 2 else p for i, p in enumerate(parts))


def sorted_by_path(nodes):
    """Tree order: each node straight after its parent, siblings in natural
    order. Sets ``depth`` on each node (0 for the top level)."""
    keyed = []
    for n in nodes:
        chain = n.ancestors()
        n.depth = len(chain) - 1
        keyed.append((tuple((natural_key(a.name), a.pk or 0) for a in chain), n))
    return [n for _, n in sorted(keyed, key=lambda item: item[0])]


def get_or_create_path(model, path, **defaults):
    """Get or create ``A > B > C`` and return the leaf (used by demo data).

    A top-level node with the leaf's name, e.g. from older data, is moved
    under its new parent instead of being duplicated.
    """
    names = path.split(' > ')
    node = None
    for i, name in enumerate(names):
        found = model.objects.filter(name=name, parent=node).first()
        if found is None and node is not None and i == len(names) - 1:
            found = model.objects.filter(name=name, parent=None).first()
            if found:
                found.parent = node
                found.save(update_fields=['parent'])
        node = found or model.objects.create(name=name, parent=node, **(defaults if i == len(names) - 1 else {}))
    return node
