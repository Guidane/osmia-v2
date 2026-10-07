from django.conf import settings
from django.test.runner import DiscoverRunner


class OsmiaTestRunner(DiscoverRunner):
    """``manage.py test`` with no labels runs Osmia's own tests and every module's.

    Modules live in ``modules/<label>/``, which isn't a package, so the default
    discovery from the project folder wouldn't find their tests."""

    def build_suite(self, test_labels=None, **kwargs):
        if not test_labels:
            test_labels = ['core', 'users', 'osmia', *settings.OSMIA_MODULES]
        return super().build_suite(test_labels, **kwargs)
