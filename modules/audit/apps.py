from core.modules import OsmiaModuleConfig


class AuditConfig(OsmiaModuleConfig):
    name = 'audit'

    def ready(self):
        super().ready()
        from core import audit
        audit.connect()
