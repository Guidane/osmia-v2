from django.contrib.auth import get_user_model

from core.trees import get_or_create_path

from .models import Department, Membership

# As in Waggle V3, plus a Field Service team.
DEPARTMENTS = [
    'Operations > Warehouse', 'Operations > Logistics', 'Operations > Field Service',
    'Engineering > R&D', 'Engineering > QA', 'Marketing',
]

# The demo users (from the Users demo data) and where they work.
PEOPLE = {
    'alice': 'Operations',
    'bob': 'Operations > Warehouse',
    'carla': 'Operations > Field Service',
}


def load():
    """Additive: creates missing departments and places demo users that have none."""
    for path in DEPARTMENTS:
        get_or_create_path(Department, path)
    for user in get_user_model().objects.filter(username__in=PEOPLE, department_membership=None):
        Membership.objects.create(user=user, department=get_or_create_path(Department, PEOPLE[user.username]))
