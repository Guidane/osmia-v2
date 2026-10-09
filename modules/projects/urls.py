from django.urls import path

from . import views

urlpatterns = [
    path('', views.ProjectListView.as_view(), name='list'),
    path('new/', views.project_form, name='create'),
    path('<int:pk>/', views.ProjectDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.project_form, name='edit'),
    path('<int:pk>/tasks/add/', views.add_task, name='add_task'),
    path('<int:pk>/tasks/<int:task_pk>/remove/', views.remove_task, name='remove_task'),
    path('task/<int:task_pk>/', views.set_task_project, name='set_task_project'),
]
