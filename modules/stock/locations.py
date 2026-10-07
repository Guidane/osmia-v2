"""Generating a block of locations, e.g. 4 rack rows × 6 racks × 4 shelves.

Each level has a name ("Shelf"), a count and a label style; every location
created is named "<level name> <label>" and its code joins the labels down
the tree: rack row A, rack 1, shelf A is A1A.
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Location

STYLES = {
    'A': 'A, B, C …',
    'a': 'a, b, c …',
    '1': '1, 2, 3 …',
    '01': '01, 02, 03 …',
}
MAX_LEVELS = 6
MAX_LOCATIONS = 5000


def letters_to_index(text):
    """'A' -> 0, 'Z' -> 25, 'AA' -> 26 (like spreadsheet columns)."""
    n = 0
    for ch in text.upper():
        if not 'A' <= ch <= 'Z':
            raise ValueError(text)
        n = n * 26 + (ord(ch) - 64)
    if not n:
        raise ValueError(text)
    return n - 1


def index_to_letters(i):
    out = ''
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        out = chr(65 + rem) + out
    return out


def labels(style, count, start=''):
    """The labels of one level, e.g. labels('A', 3) == ['A', 'B', 'C']."""
    start = (start or '').strip()
    if style in ('A', 'a'):
        first = letters_to_index(start) if start else 0
        result = [index_to_letters(first + i) for i in range(count)]
        return [x.lower() for x in result] if style == 'a' else result
    first = int(start) if start else 1
    numbers = [first + i for i in range(count)]
    if style == '01':
        width = max(2, len(str(numbers[-1])) if numbers else 2)
        return [str(n).zfill(width) for n in numbers]
    return [str(n) for n in numbers]


def clean_levels(rows):
    """Check the levels from the form: [{'name', 'count', 'style', 'start'}]."""
    levels, errors = [], []
    for i, row in enumerate(rows, 1):
        name = (row.get('name') or '').strip()
        if not name and not str(row.get('count') or '').strip():
            continue  # an empty row
        try:
            count = int(row.get('count'))
        except (TypeError, ValueError):
            errors.append(f'Level {i}: "how many" must be a whole number.')
            continue
        style = row.get('style') if row.get('style') in STYLES else 'A'
        if not name:
            errors.append(f'Level {i}: give it a name, e.g. Rack or Shelf.')
        if not 1 <= count <= 500:
            errors.append(f'Level {i}: "how many" must be between 1 and 500.')
            continue
        try:
            level_labels = labels(style, count, row.get('start'))
        except ValueError:
            wanted = 'a letter, e.g. A' if style in ('A', 'a') else 'a number, e.g. 1'
            errors.append(f'Level {i}: "start at" must be {wanted}.')
            continue
        if any(len(label) > 20 for label in level_labels):
            errors.append(f'Level {i}: the labels get too long.')
            continue
        levels.append({'name': name, 'labels': level_labels})
    if not levels and not errors:
        errors.append('Add at least one level.')
    if len(levels) > MAX_LEVELS:
        errors.append(f'At most {MAX_LEVELS} levels at once.')
    total = count_locations(levels)
    if total > MAX_LOCATIONS:
        errors.append(f'That would be {total} locations; make at most {MAX_LOCATIONS} at once.')
    if errors:
        raise ValidationError(errors)
    return levels


def count_locations(levels):
    total, product = 0, 1
    for level in levels:
        product *= len(level['labels'])
        total += product
    return total


@transaction.atomic
def generate(parent, levels):
    """Create the locations under ``parent`` (None for the top). Ones that
    already exist (same place, same label) are kept, so running it again,
    e.g. with more racks, only adds what's missing. Returns (created, existing)."""
    created = existing = 0

    def build(under, depth):
        nonlocal created, existing
        if depth == len(levels):
            return
        level = levels[depth]
        have = {loc.label.lower(): loc for loc in Location.objects.filter(parent=under).exclude(label='')}
        for label in level['labels']:
            node = have.get(label.lower())
            if node is None:
                node = Location(name=f'{level["name"]} {label}', label=label, parent=under)
                node.save()
                created += 1
            else:
                existing += 1
            node.parent = under  # reuse the parent object: no lookups on the way down
            build(node, depth + 1)

    build(parent, 0)
    return created, existing
