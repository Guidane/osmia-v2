"""Look up a part's attributes online by its part number (no prices).

Providers are small functions that take a part number and return a
``PartInfo`` (or None when nothing matches). Only Mouser is built in; add
others (DigiKey, Nexar, ...) to ``PROVIDERS``.

Mouser needs a free Search API key (mouser.com > Services > APIs), set as the
``OSMIA_MOUSER_API_KEY`` environment variable.
"""
import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field

from django.conf import settings


class LookupNotConfigured(Exception):
    """No provider has the credentials it needs."""


class LookupFailed(Exception):
    """The provider was reached but returned an error, or couldn't be reached."""


@dataclass
class PartInfo:
    part_number: str
    source: str
    description: str = ''
    manufacturer: str = ''
    manufacturer_part_number: str = ''
    category: str = ''
    datasheet_url: str = ''
    product_url: str = ''
    attributes: dict = field(default_factory=dict)  # {name: value}, in the provider's order

    def as_dict(self):
        return asdict(self)


# -- Mouser ----------------------------------------------------------------------

MOUSER_URL = 'https://api.mouser.com/api/v1/search/partnumber'


def mouser(part_number, *, timeout=10):
    key = getattr(settings, 'OSMIA_MOUSER_API_KEY', '')
    if not key:
        raise LookupNotConfigured('Set the OSMIA_MOUSER_API_KEY environment variable to look parts up on Mouser.')
    body = json.dumps({'SearchByPartRequest': {'mouserPartNumber': part_number, 'partSearchOptions': 'Exact'}})
    request = urllib.request.Request(
        f'{MOUSER_URL}?apiKey={key}', data=body.encode('utf-8'), method='POST',
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise LookupFailed(f'Mouser answered with HTTP {exc.code}.') from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LookupFailed(f'Could not reach Mouser: {getattr(exc, "reason", exc)}.') from exc
    except ValueError as exc:
        raise LookupFailed('Mouser sent a response that is not JSON.') from exc
    return parse_mouser(part_number, data)


def parse_mouser(part_number, data):
    errors = data.get('Errors') or []
    if errors:
        messages = '; '.join(e.get('Message') or e.get('Code') or 'unknown error' for e in errors)
        raise LookupFailed(f'Mouser: {messages}')
    parts = ((data.get('SearchResults') or {}).get('Parts')) or []
    if not parts:
        return None
    wanted = part_number.strip().lower()
    part = next(
        (p for p in parts if wanted in {(p.get('ManufacturerPartNumber') or '').lower(), (p.get('MouserPartNumber') or '').lower()}),
        parts[0],
    )

    attributes = {}
    for attr in part.get('ProductAttributes') or []:
        name, value = (attr.get('AttributeName') or '').strip(), (attr.get('AttributeValue') or '').strip()
        if not name or not value:
            continue
        # Mouser can repeat a name (e.g. several Packaging values); keep them all.
        attributes[name] = f'{attributes[name]}, {value}' if name in attributes and value not in attributes[name] else value
    extra = {
        'Manufacturer': part.get('Manufacturer') or '',
        'Manufacturer part number': part.get('ManufacturerPartNumber') or '',
        'Datasheet': part.get('DataSheetUrl') or '',
        'RoHS': part.get('ROHSStatus') or '',
        'Lifecycle': part.get('LifecycleStatus') or '',
    }
    for name, value in extra.items():
        if value and name not in attributes:
            attributes[name] = value

    return PartInfo(
        part_number=part_number,
        source='Mouser',
        description=part.get('Description') or '',
        manufacturer=part.get('Manufacturer') or '',
        manufacturer_part_number=part.get('ManufacturerPartNumber') or '',
        category=part.get('Category') or '',
        datasheet_url=part.get('DataSheetUrl') or '',
        product_url=part.get('ProductDetailUrl') or '',
        attributes=attributes,
    )


PROVIDERS = [mouser]


def lookup(part_number):
    """Ask each configured provider in turn; return the first match or None.

    Raises LookupNotConfigured when no provider is set up, LookupFailed when
    every configured provider failed.
    """
    configured, failures = False, []
    for provider in PROVIDERS:
        try:
            info = provider(part_number)
        except LookupNotConfigured:
            continue
        except LookupFailed as exc:
            configured = True
            failures.append(str(exc))
            continue
        configured = True
        if info is not None:
            return info
    if not configured:
        raise LookupNotConfigured('No part lookup is set up. Set OSMIA_MOUSER_API_KEY to use Mouser.')
    if failures:
        raise LookupFailed(' '.join(failures))
    return None
