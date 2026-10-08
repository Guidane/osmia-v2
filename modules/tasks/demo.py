from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import Task, TaskGroup

S, P = Task.Status, Task.Priority
C = TaskGroup.Color

GROUPS = {
    'Inventory': (C.BLUE, 'Stock counts, putting away, bins and reorder levels'),
    'Production': (C.ORANGE, 'Building units for customers'),
    'Repairs': (C.PINK, 'Returned units (RMA)'),
    'Quality': (C.GREEN, 'Calibration, audits and test procedures'),
    'Maintenance': (C.SLATE, 'Keeping the workshop and its machines running'),
    'Training': (C.PURPLE, 'Courses and instruction for the team'),
    'Purchasing': (C.AMBER, 'Quotes, suppliers and consumables'),
}


def t(title, who, start, due, *subtasks, status=None, priority=P.NORMAL, late=False):
    """A demo task, starting and due N days from today. Without a status it
    follows the dates: done in the past, in progress now, to do later; with
    ``late`` it isn't done although it's due (overdue)."""
    if status is None:
        status = (S.TODO if late else S.DONE) if due < 0 else S.IN_PROGRESS if start <= 0 else S.TODO
    return (title, who, status, priority, start, due, list(subtasks))


def build(n, start):
    """Build PDU-100 #n: the same steps for every unit."""
    return t(f'Build PDU-100 #{n}', 'carla', start, start + 9,
             t('Kit the parts from stock', 'bob', start, start + 1),
             t('Make the J01-J04 harnesses', 'carla', start + 2, start + 4),
             t('Assemble and wire the chassis', 'carla', start + 4, start + 7),
             t('Final test and test report', 'alice', start + 8, start + 9),
             priority=P.HIGH)


# (title, assignee, status, priority, starts in N days, due in N days, subtasks) per group.
# Subtasks are rows of the same shape, nested as deep as needed; they're in their parent's group.
TASKS = {
    'Inventory': [
        t('Quarterly stock count', 'alice', -5, 2,
          t('Count aisle 1', 'alice', -5, -3),
          t('Count aisle 2', 'bob', -3, 0,
            t('Shelf 2A: resistors and capacitors', 'bob', -3, -2),
            t('Shelf 2B: connectors', 'bob', -2, 0),
            t('Shelf 2C: cable and sleeving', 'carla', -1, 0, status=S.TODO)),
          t('Book the differences in Stock', 'alice', 1, 2),
          priority=P.HIGH),
        t('Sort the resistor drawers by value', 'bob', 3, 10),
        t('Put away the Farnell delivery', 'bob', -12, -11),
        t('Put away the RS delivery', 'bob', -6, -5),
        t('Put away the Würth delivery', 'bob', -1, 0),
        t('Put away the Mouser delivery', 'bob', 4, 4),
        t('Label the bins in aisle 3', 'bob', -8, 5,
          t('Print the bin labels', 'alice', -8, -7),
          t('Stick labels on shelves 3A-3C', 'bob', -6, 1, late=True),
          t('Update the location codes in Stock', 'alice', 2, 5)),
        t('Set reorder levels for connectors', 'alice', 6, 9),
        t('Scrap the obsolete relays', 'bob', -20, -18),
        t('Move the cable stock to the new racks', 'bob', 12, 20,
          t('Assemble the cable racks', 'bob', 12, 14),
          t('Move the reels', 'bob', 15, 18),
          t('Update the locations in Stock', 'alice', 19, 20)),
        t('Check the dry cabinet humidity log', 'alice', -2, -2, late=True),
    ],
    'Production': [
        build(3, -21),
        build(4, -9),
        build(5, 2),
        build(6, 14),
        t('Plan the Q1 production schedule', 'alice', 20, 27, priority=P.HIGH),
    ],
    'Repairs': [
        t('Repair returned PDU-100 #2', 'carla', -4, -1,
          t('Diagnose the fault', 'carla', -4, -3),
          t('Replace the damaged outlet board', 'carla', -2, -1,
            t('Order a spare outlet board', 'alice', -3, -3),
            t('Swap the board and re-crimp the J01 harness', 'carla', -1, -1, late=True)),
          t('Hi-pot and function test', 'carla', -1, -1, late=True),
          status=S.TODO, priority=P.HIGH),
        t('Repair RMA-1042 load bank', 'carla', -15, -6,
          t('Replace the fan', 'carla', -15, -12),
          t('Burn-in test, 24 h', 'carla', -8, -6)),
        t('Repair RMA-1047 control box', 'carla', 5, 9),
        t('Write the RMA report for customer 1042', 'alice', -5, -4),
    ],
    'Quality': [
        t('Calibrate the electronic load', 'carla', -9, -3),
        t('Calibrate the multimeters', 'carla', -2, 6,
          t('Fluke 87V #1', 'carla', -2, -1),
          t('Fluke 87V #2', 'carla', 0, 1),
          t('Bench meter', 'carla', 4, 6)),
        t('Annual check of the hi-pot tester', 'carla', 18, 18),
        t('Internal audit: ESD area', 'alice', 7, 16,
          t('Measure the floor and bench resistance', 'bob', 7, 8),
          t('Check the wrist strap test log', 'alice', 9, 10),
          t('Write the audit report', 'alice', 14, 16)),
        t('Review test procedure TP-100', 'alice', -10, -7),
        t('Update the harness crimp height spec', 'alice', 25, 30),
    ],
    'Maintenance': [
        t('Service the crimp press', 'bob', -16, -15),
        t('Replace the bench lighting', 'bob', 3, 12,
          t('Order LED panels', 'alice', 3, 4),
          t('Fit the panels on benches 1-3', 'bob', 8, 10),
          t('Fit the panels on benches 4-6', 'bob', 11, 12)),
        t('Clean the solder fume extractors', 'bob', -3, -3, late=True),
        t('Check the compressor', 'bob', 22, 22),
        t('Replace the soldering tips', 'carla', 1, 1),
        t('Test the RCDs on the benches', 'bob', 15, 15),
        t('Recharge the fire extinguishers', 'alice', -25, -24),
    ],
    'Training': [
        t('ESD training for new staff', 'alice', 8, 21,
          t('Book the trainer', 'alice', 0, 3),
          t('Prepare the ESD kits', 'bob', 10, 18,
            t('Test the wrist straps', 'bob', 10, 14),
            t('Order spare heel straps', 'alice', 10, 12)),
          t('Run the session', 'alice', 21, 21),
          priority=P.LOW),
        t('Crimp tool training for Bob', 'carla', -7, 2,
          t('D-sub contacts', 'carla', -7, -6),
          t('Ring terminals and ferrules', 'carla', 1, 2)),
        t('IPC/WHMA-A-620 refresher', 'carla', 30, 32, priority=P.LOW),
    ],
    'Purchasing': [
        t('Quotes for a new test rack', 'alice', -4, 8,
          t('Write the requirements', 'alice', -4, -2),
          t('Ask three suppliers for a quote', 'alice', -1, 4),
          t('Compare the quotes', 'alice', 6, 8)),
        t('Order Q4 consumables', 'alice', 0, 5,
          t('Solder wire and flux', 'alice', 0, 1),
          t('Heat shrink and sleeving', 'alice', 0, 1),
          t('Cable ties and labels', 'alice', 2, 3),
          t('Gloves and wipes', 'alice', 4, 5)),
        t('Find a second source for DB25 shells', 'alice', 9, 15),
        t('Renew the calibration service contract', 'alice', 26, 28),
    ],
    None: [  # not in a group
        t('Tidy the meeting room', 'bob', 1, 1, priority=P.LOW),
        t('Update the phone list', 'alice', -1, -1, late=True, priority=P.LOW),
        t('Plan the summer party', 'alice', 40, 50, priority=P.LOW),
    ],
}


def load():
    """Additive: creates the groups and the demo tasks that are missing (by title),
    also the subtasks of demo tasks loaded before tasks had them."""
    users = {u.username: u for u in get_user_model().objects.all()}
    groups = {}
    for name, (color, description) in GROUPS.items():
        groups[name], _ = TaskGroup.objects.get_or_create(name=name, defaults={'color': color, 'description': description})
    for group_name, rows in TASKS.items():
        group = groups.get(group_name)
        for row in rows:
            task = Task.objects.filter(title=row[0], parent=None).first()
            if task is None:
                create_tree(row, None, group, users)
                continue
            if not task.subtasks.exists():
                for child in row[6]:
                    create_tree(child, task, group, users)
            if group and task.group_id is None:
                task.move_to_group(group)
    # Tasks without a department take their assignee's.
    for task in Task.objects.filter(department=None, assignee__department_membership__isnull=False).select_related(
            'assignee__department_membership'):
        task.department = task.assignee.department_membership.department
        task.save(update_fields=['department'])


def create_tree(row, parent, group, users):
    title, who, status, priority, start_in, due_in, subtasks = row
    today = timezone.localdate()
    task = Task.objects.create(
        title=title, parent=parent, group=group, assignee=users.get(who), created_by=users.get('admin'),
        status=status, priority=priority,
        start_date=today + timedelta(days=start_in), due_date=today + timedelta(days=due_in),
    )
    for child in subtasks:
        create_tree(child, task, group, users)
    return task
