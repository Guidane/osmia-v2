from django.urls import path

from . import views

urlpatterns = [
    path('', views.DepartmentListView.as_view(), name='list'),
    path('new/', views.DepartmentCreateView.as_view(), name='create'),
    path('<int:pk>/', views.DepartmentDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.DepartmentUpdateView.as_view(), name='edit'),
]
