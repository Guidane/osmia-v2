from django.urls import path

from . import views

urlpatterns = [
    path('', views.AssemblyListView.as_view(), name='list'),
    path('new/', views.assembly_form, name='create'),
    path('<int:pk>/', views.AssemblyDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.assembly_form, name='edit'),
    path('<int:pk>/build-task/', views.create_build_task, name='create_build_task'),
    path('link-task/<int:task_pk>/', views.link_task, name='link_task'),
]
