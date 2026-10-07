"""Importing a connector's pin table from a CSV file (Edit pins > Import CSV).

The format is described for people and AI assistants in devices/AI_README.md.
In short: a header row, then one row per pin. Columns are matched by name
(case and spacing don't matter) and may come in any order:

    Pin        required: the pin's label as printed on the connector (1, 2, A1, S...)
    Signal     optional: one of Devices > Signals; unknown signals are added there
    Tag 1..4   optional ("Tag" alone means Tag 1): this connector's own tags
    Set        optional: set number; pins with the same number are one cable set
    Set type   optional: straight, twisted, shielded or twisted shielded

Comma, semicolon and tab separated files all work.
"""
import csv
import io
import re

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Connector, Pin, Signal, TagOption

MAX_ROWS = 1000
MAX_BYTES = 1024 * 1024

HEADERS = {
    'pin': 'label', 'pin label': 'label', 'pin number': 'label', 'pin no': 'label', 'pin nr': 'label', 'label': 'label',
    'contact': 'label', 'position': 'label',
    'signal': 'signal', 'signal name': 'signal', 'signal type': 'signal',
    'tag': 'tag1', 'tag 1': 'tag1', 'tag1': 'tag1', 'tag 2': 'tag2', 'tag2': 'tag2',
    'tag 3': 'tag3', 'tag3': 'tag3', 'tag 4': 'tag4', 'tag4': 'tag4',
    'set': 'set_number', 'set number': 'set_number', 'set no': 'set_number', 'set nr': 'set_number', 'pair': 'set_number',
    'set type': 'set_type', 'settype': 'set_type', 'pair type': 'set_type', 'cable type': 'set_type',
}

SET_TYPES = {
    'straight': 'straight', 'single': 'straight', 'none': 'straight', 'plain': 'straight',
    'twisted': 'twisted', 'twisted pair': 'twisted', 'tp': 'twisted', 'utp': 'twisted',
    'shielded': 'shielded', 'screened': 'shielded',
    'twisted shielded': 'twisted_shielded', 'shielded twisted': 'twisted_shielded', 'twisted_shielded': 'twisted_shielded',
    'twisted + shielded': 'twisted_shielded', 'shielded twisted pair': 'twisted_shielded', 'stp': 'twisted_shielded',
    'twisted shielded pair': 'twisted_shielded',
}


def _norm(text):
    return re.sub(r'[\s_\-.]+', ' ', str(text or '').strip().lower()).strip()


def read_rows(upload):
    """[{field: value}] from an uploaded CSV, plus problems found on the way."""
    raw = upload.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValidationError('The file is larger than 1 MB; a pin table is much smaller than that.')
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValidationError('The file is not text (save it as CSV from your spreadsheet).')
    if not text.strip():
        raise ValidationError('The file is empty.')
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        raise ValidationError('The file has no rows.')
    header = [HEADERS.get(_norm(h), None) for h in rows[0]]
    if 'label' not in header:
        found = ', '.join(f'"{h}"' for h in rows[0]) or 'nothing'
        raise ValidationError(f'The first row must name the columns, with one called "Pin". Found {found}.')
    unknown = [h for h, f in zip(rows[0], header) if f is None and h.strip()]
    data = []
    for line_no, row in enumerate(rows[1:], start=2):
        if len(data) >= MAX_ROWS:
            raise ValidationError(f'More than {MAX_ROWS} pins; split the file.')
        record = {'_line': line_no}
        for field, cell in zip(header, row):
            if field and field not in record:
                record[field] = cell.strip()
        data.append(record)
    return data, unknown


def clean_rows(rows):
    """Check the rows: (pins, problems). Raises ValidationError if nothing can be imported."""
    pins, problems, seen = [], [], {}
    for r in rows:
        label = (r.get('label') or '')[:20]
        if not label:
            problems.append(f'Line {r["_line"]}: no pin label, skipped.')
            continue
        if label.lower() in seen:
            raise ValidationError(f'Pin {label} is in the file twice (lines {seen[label.lower()]} and {r["_line"]}).')
        seen[label.lower()] = r['_line']
        pin = {'label': label}
        if 'signal' in r:
            pin['signal'] = r['signal'][:50]
        for i in range(1, 5):
            if f'tag{i}' in r:
                pin[f'tag{i}'] = r[f'tag{i}'][:50]
        if 'set_number' in r:
            number = r['set_number'].strip()
            pin['set_number'] = None
            if number:
                if number.isdigit() and int(number) > 0:
                    pin['set_number'] = int(number)
                else:
                    problems.append(f'Line {r["_line"]}: set "{number}" is not a whole number, left empty.')
        if 'set_type' in r:
            kind = r['set_type'].strip()
            set_type = SET_TYPES.get(_norm(kind).replace(' + ', ' '), None) if kind else ''
            if set_type is None:
                problems.append(f'Line {r["_line"]}: set type "{kind}" not known (use straight, twisted, shielded or twisted shielded), left empty.')
                set_type = ''
            pin['set_type'] = set_type
        pins.append(pin)
    if not pins:
        raise ValidationError('No pins to import.')
    # One type per set, as in the editor.
    types = {}
    for p in pins:
        if p.get('set_number') and p.get('set_type'):
            types.setdefault(p['set_number'], p['set_type'])
    for p in pins:
        number = p.get('set_number')
        if number and types.get(number) and p.get('set_type') != types[number]:
            if p.get('set_type'):
                problems.append(f'Pin {p["label"]}: set {number} has more than one type; used {types[number].replace("_", " ")}.')
            p['set_type'] = types[number]
    return pins, problems


@transaction.atomic
def apply(connector, pins, replace=False):
    """Write the pins to the connector. ``replace``: the file is the whole pin
    table (pins not in it are removed, the file's order is used). Otherwise
    pins are matched by label and updated; new ones are added at the end.
    Returns a summary dict."""
    added_signals = []
    known = {s.name.upper(): s.name for s in Signal.objects.all()}
    for p in pins:
        name = p.get('signal')
        if name:
            if name.upper() in known:
                p['signal'] = known[name.upper()]
            else:
                Signal.objects.create(name=name)
                known[name.upper()] = name
                added_signals.append(name)
    existing = {pin.label.lower(): pin for pin in connector.pins.all()}
    updated = created = removed = 0
    fields = ['signal', 'tag1', 'tag2', 'tag3', 'tag4', 'set_number', 'set_type']
    if replace:
        keep = {p['label'].lower() for p in pins}
        for label, pin in list(existing.items()):
            if label not in keep:
                pin.delete()
                removed += 1
                del existing[label]
        # Renumber in the file's order, clear of the unique (connector, position) constraint.
        from django.db.models import F
        Pin.objects.filter(connector=connector).update(position=F('position') + 100000)
    next_position = (max((pin.position for pin in existing.values()), default=0) + 1) if not replace else None
    for i, p in enumerate(pins, start=1):
        pin = existing.get(p['label'].lower())
        if pin is None:
            pin = Pin(connector=connector, label=p['label'])
            created += 1
            if replace:
                pin.position = i
            else:
                pin.position = next_position
                next_position += 1
        else:
            updated += 1
            if replace:
                pin.position = i
        for f in fields:
            if f in p:  # only the columns the file has; others keep their values
                setattr(pin, f, p[f])
        pin.save()
    used = max([i for p in pins for i in range(1, 5) if p.get(f'tag{i}')] + [1])
    if used > connector.tag_columns:
        connector.tag_columns = used
        connector.save(update_fields=['tag_columns'])
    TagOption.from_pins(connector)
    return {'created': created, 'updated': updated, 'removed': removed, 'added_signals': added_signals}
