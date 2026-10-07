from django.urls import path

from . import views

urlpatterns = [
    path('', views.ToolListView.as_view(), name='list'),
    path('new/', views.ToolCreateView.as_view(), name='create'),
    path('<int:pk>/', views.ToolDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.ToolUpdateView.as_view(), name='edit'),
]
