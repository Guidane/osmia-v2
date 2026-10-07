from django.urls import path

from . import module_views, setup, views

app_name = 'core'

urlpatterns = [
    path('', views.home, name='home'),
    path('setup/', setup.setup, name='setup'),
    path('images/<int:pk>/', views.image_file, name='image'),
    path('images/<int:pk>/update/', views.image_update, name='image_update'),
    path('images/add/<str:model>/<int:pk>/', views.image_upload, name='image_upload'),
    # Installing, upgrading and removing modules (osmia/addons.py)
    path('modules/', module_views.module_list, name='modules'),
    path('modules/install/', module_views.module_install, name='modules_install'),
    path('modules/upload/', module_views.module_upload, name='modules_upload'),
    path('modules/update-all/', module_views.module_update_all, name='modules_update_all'),
    path('modules/cancel/', module_views.module_cancel, name='modules_cancel'),
    path('modules/applying/', module_views.module_applying, name='modules_applying'),
    path('modules/status/', module_views.module_status, name='modules_status'),
    path('modules/<str:label>/download/', module_views.module_download, name='modules_download'),
    path('modules/<str:label>/<str:action>/', module_views.module_change, name='modules_change'),
]
