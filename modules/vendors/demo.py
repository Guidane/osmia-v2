from .models import Vendor

VENDORS = [
    # name, code, kind, website, city, country, payment terms, contacts (name, role, email)
    ('Connector Supply Co.', 'CSC', Vendor.Kind.DISTRIBUTOR, 'https://example.com', 'Berlin', 'Germany', '30 days net',
     [('Anna Weber', 'Sales', 'anna.weber@example.com')]),
    ('Mouser Electronics', 'MOU', Vendor.Kind.DISTRIBUTOR, 'https://www.mouser.com', 'Mansfield, TX', 'USA',
     'Credit card', []),
    ('TE Connectivity', 'TE', Vendor.Kind.MANUFACTURER, 'https://www.te.com', 'Schaffhausen', 'Switzerland', '', []),
    ('Mean Well', 'MW', Vendor.Kind.MANUFACTURER, 'https://www.meanwell.com', 'New Taipei', 'Taiwan', '', []),
]


def load():
    for name, code, kind, web, city, country, terms, contacts in VENDORS:
        vendor, created = Vendor.objects.get_or_create(name=name, defaults={
            'code': code, 'kind': kind, 'website': web, 'city': city, 'country': country, 'payment_terms': terms,
        })
        if created:
            for person, role, email in contacts:
                vendor.contacts.create(name=person, role=role, email=email)
