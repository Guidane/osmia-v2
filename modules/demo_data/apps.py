from core.modules import OsmiaModuleConfig


class DemoDataConfig(OsmiaModuleConfig):
    name = 'demo_data'

    def ready(self):
        super().ready()
        from django.db.models.signals import post_save

        from .models import track
        post_save.connect(track, dispatch_uid='demo-data-track')
