from django.urls import path

from . import views

urlpatterns = [
    path('', views.board, name='board'),
    path('list/', views.TaskListView.as_view(), name='list'),
    path('gantt/', views.gantt, name='gantt'),
    path('mine/', views.TaskListView.as_view(mine=True), name='mine'),
    path('new/', views.TaskCreateView.as_view(), name='create'),
    path('<int:pk>/', views.TaskDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.TaskUpdateView.as_view(), name='edit'),
    path('<int:pk>/delete/', views.TaskDeleteView.as_view(), name='delete'),
    path('<int:pk>/status/', views.set_status, name='set_status'),
]
