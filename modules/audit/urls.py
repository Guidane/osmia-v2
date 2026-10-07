from django.urls import path

from . import views

urlpatterns = [
    path('', views.LogListView.as_view(), name='list'),
    path('chains/', views.ChainListView.as_view(), name='chains'),
    path('chains/<str:chain_id>/', views.ChainDetailView.as_view(), name='chain'),
    path('history/<int:ct>/<int:pk>/', views.HistoryView.as_view(), name='history'),
    path('activity/', views.ActivityView.as_view(), name='activity'),
    path('settings/', views.SettingsView.as_view(), name='settings'),
]
