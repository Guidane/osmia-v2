from core.modules import OsmiaModuleConfig


class AutomationsConfig(OsmiaModuleConfig):
    name = 'automations'

    def ready(self):
        super().ready()
        from core import automation

        from . import engine
        automation.subscribe(engine.handle)
