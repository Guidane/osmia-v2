from django.urls import path

from . import views

urlpatterns = [
    path('', views.board, name='board'),
    path('list/', views.TaskListView.as_view(), name='list'),
    path('gantt/', views.gantt, name='gantt'),
    path('mine/', views.TaskListView.as_view(mine=True), name='mine'),
    path('new/', views.TaskCreateView.as_view(), name='create'),
    path('move/', views.move_to_group, name='move'),
    path('groups/', views.GroupListView.as_view(), name='groups'),
    path('groups/new/', views.GroupCreateView.as_view(), name='group_create'),
    path('groups/<int:pk>/edit/', views.GroupUpdateView.as_view(), name='group_edit'),
    path('groups/<int:pk>/delete/', views.GroupDeleteView.as_view(), name='group_delete'),
    path('<int:pk>/', views.TaskDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.TaskUpdateView.as_view(), name='edit'),
    path('<int:pk>/delete/', views.TaskDeleteView.as_view(), name='delete'),
    path('<int:pk>/status/', views.set_status, name='set_status'),
]
