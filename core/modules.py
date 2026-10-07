"""
The Osmia module system.

A module is an ordinary Django app whose AppConfig subclasses
``OsmiaModuleConfig``, with a ``manifest.json`` next to its ``apps.py``,
much like an Odoo ``__manifest__.py``. The registry below reads those
manifests to build the navigation, mount each module's URLs and order modules
by dependency. Installing modules is osmia/addons.py's job.
"""
import json
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

from django.apps import AppConfig, apps


@dataclass(frozen=True)
class MenuItem:
    label: str
    url_name: str  # e.g. "tasks:list"


@dataclass(frozen=True)
class Module:
    title: str
    description: str = ""
    icon: str = "📦"
    version: str = "1.0.0"
    depends: tuple[str, ...] = ()
    menu: tuple[MenuItem, ...] = ()
    sequence: int = 100
    url_prefix: str | None = None  # defaults to the app label
    home_url: str | None = None  # defaults to the first menu item

    @classmethod
    def from_json(cls, path):
        """The Module described by a ``manifest.json``."""
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return cls(
            title=data['title'],
            description=data.get('description', ''),
            icon=data.get('icon') or cls.icon,
            version=str(data.get('version', cls.version)),
            depends=tuple(data.get('depends', ())),
            menu=tuple(MenuItem(e['label'], e['url']) for e in data.get('menu', ())),
            sequence=int(data.get('sequence', 100)),
            url_prefix=data.get('url_prefix'),
            home_url=data.get('home_url'),
        )


class OsmiaModuleConfig(AppConfig):
    """Base AppConfig for every Osmia module. The manifest is read from the
    module's ``manifest.json`` unless the class sets one."""

    manifest: Module
    default_auto_field = 'django.db.models.BigAutoField'

    # Modules import this base class into their apps.py, so Django would see two
    # AppConfig candidates and pick neither. Mark the base as non-default and
    # every subclass as default so the module's own config is always chosen.
    default = False

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.default = cls.__dict__.get('default', True)

    def __init__(self, app_name, app_module):
        super().__init__(app_name, app_module)
        if 'manifest' not in vars(type(self)):
            self.manifest = Module.from_json(Path(self.path) / 'manifest.json')

    @property
    def prefix(self):
        return self.manifest.url_prefix if self.manifest.url_prefix is not None else self.label

    @property
    def home_url_name(self):
        if self.manifest.home_url:
            return self.manifest.home_url
        return self.manifest.menu[0].url_name if self.manifest.menu else None


def installed_modules():
    """Installed modules ordered by sequence, then title."""
    configs = [c for c in apps.get_app_configs() if isinstance(c, OsmiaModuleConfig)]
    return sorted(configs, key=lambda c: (c.manifest.sequence, c.manifest.title))


def get_module(label):
    for config in installed_modules():
        if config.label == label:
            return config
    return None


def dependency_order():
    """Modules sorted so that every module comes after its dependencies."""
    by_label = {c.label: c for c in installed_modules()}
    ordered, seen = [], set()

    def visit(config, stack=()):
        if config.label in seen:
            return
        if config.label in stack:
            raise RuntimeError(f"Circular module dependency: {' -> '.join(stack + (config.label,))}")
        for dep in config.manifest.depends:
            if dep in by_label:
                visit(by_label[dep], stack + (config.label,))
        seen.add(config.label)
        ordered.append(config)

    for config in by_label.values():
        visit(config)
    return ordered


def module_urlpatterns():
    """Mount ``<app>/urls.py`` of every module under its prefix, namespaced by label."""
    from django.urls import include, path

    patterns = []
    for config in installed_modules():
        if find_spec(f"{config.name}.urls") is None:
            continue
        prefix = f"{config.prefix}/" if config.prefix else ""
        patterns.append(path(prefix, include((f"{config.name}.urls", config.label))))
    return patterns
