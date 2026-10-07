from django.urls import path

from . import views

urlpatterns = [
    path('', views.RuleListView.as_view(), name='list'),
    path('new/', views.rule_form, name='create'),
    path('<int:pk>/', views.RuleDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.rule_form, name='edit'),
    path('<int:pk>/toggle/', views.toggle, name='toggle'),
    path('<int:pk>/delete/', views.RuleDeleteView.as_view(), name='delete'),
    path('runs/', views.RunListView.as_view(), name='runs'),
    path('notifications/', views.notifications, name='notifications'),
    path('notifications/<int:pk>/', views.open_notification, name='open_notification'),
]
