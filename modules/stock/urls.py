from django.urls import path

from . import views

urlpatterns = [
    path('', views.StockListView.as_view(), name='list'),
    path('moves/', views.MoveListView.as_view(), name='move_list'),
    path('moves/new/', views.MoveCreateView.as_view(), name='move_create'),
    path('moves/transfer/', views.transfer, name='transfer'),
    path('parts/<int:part_pk>/settings/', views.part_settings, name='part_settings'),
    path('locations/', views.LocationListView.as_view(), name='location_list'),
    path('locations/new/', views.LocationCreateView.as_view(), name='location_create'),
    path('locations/generate/', views.location_generate, name='location_generate'),
    path('locations/<int:pk>/generate/', views.location_generate, name='location_generate_under'),
    path('locations/<int:pk>/', views.LocationDetailView.as_view(), name='location_detail'),
    path('locations/<int:pk>/edit/', views.LocationUpdateView.as_view(), name='location_edit'),
    path('locations/<int:pk>/delete/', views.location_delete, name='location_delete'),
]
