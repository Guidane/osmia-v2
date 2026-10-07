from django.contrib import admin
from django.urls import include, path

from core.modules import module_urlpatterns

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
    *module_urlpatterns(),
]
