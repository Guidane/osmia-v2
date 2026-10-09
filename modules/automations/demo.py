from django.apps import apps
from django.urls import reverse

from users.models import User

from .models import Notification, Rule


def load():
    # Something in the administrator's notifications (the 🔔 in the top bar) to start with.
    admin = User.objects.filter(username='admin').first()
    if admin and not Notification.objects.filter(user=admin).exists():
        Notification.objects.create(user=admin, message='Welcome to Osmia. Rules under Automations send notifications like this one.',
                                    url=reverse('automations:list'))
    load_rules()


def load_rules():
    """Two rules that chain: placing an order creates the check-in tasks, and
    finishing them receives the order into stock."""
    if Rule.objects.exists() or not (apps.is_installed('orders') and apps.is_installed('tasks')):
        return
    people = {u.username: u.pk for u in User.objects.filter(username__in=['alice', 'bob', 'carla'])}
    check_in = Rule.objects.create(
        name='Check in placed orders',
        description='When an order is placed, the warehouse gets tasks to take the delivery in.',
        trigger='orders.order_placed',
        actions=[
            {'action': 'tasks.create_tasks', 'params': {'tasks': [
                {'title': 'Take in the delivery of {source}', 'description': 'Count the parts against the order lines.',
                 'assignee': str(people.get('bob', '')), 'department': '', 'priority': '', 'due_in_days': '3'},
                {'title': 'Inspect the connectors from {source}', 'description': 'Spot-check pins and shells.',
                 'assignee': str(people.get('carla', '')), 'department': '', 'priority': '', 'due_in_days': '5'},
            ]}},
            {'action': 'automations.notify', 'params': {
                'users': [people['alice']] if 'alice' in people else [], 'owner': False, 'link': 'object',
                'message': '{object} was placed; the check-in tasks are assigned.',
            }},
        ],
    )
    Rule.objects.create(
        name='Receive orders once checked in',
        description='When all check-in tasks of an order are done, the order is received and its parts go into stock.',
        trigger='tasks.batch_completed',
        conditions=[{'field': 'rule', 'op': 'is', 'value': str(check_in.pk)}],
        actions=[
            {'action': 'core.set_status', 'params': {'target': 'source', 'status': 'orders.order:received'}},
            {'action': 'automations.notify', 'params': {
                'users': [people['alice']] if 'alice' in people else [], 'owner': False, 'link': 'source',
                'message': '{source} is checked in and received into stock.',
            }},
        ],
    )
