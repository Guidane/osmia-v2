from .models import User

# The administrator is made on the setup page; these are the demo's other people.
PEOPLE = [
    ('alice', 'Alice', 'Martin', 'Operations Manager'),
    ('bob', 'Bob', 'Nguyen', 'Warehouse Lead'),
    ('carla', 'Carla', 'Rossi', 'Technician'),
]


def load():
    """Additive: creates the demo users that are missing (password "demo").

    Only on a database without any administrator (the tests'), it also makes
    admin / admin; a real install has made its administrator on the setup page."""
    if not User.objects.filter(is_superuser=True).exists():
        User.objects.create_superuser('admin', 'admin@example.com', 'admin', first_name='Admin')
    for username, first, last, title in PEOPLE:
        if not User.objects.filter(username=username).exists():
            User.objects.create_user(username, f'{username}@example.com', 'demo', first_name=first, last_name=last,
                                     job_title=title)
