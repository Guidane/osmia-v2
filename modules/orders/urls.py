from django.urls import path

from . import views

urlpatterns = [
    path('', views.OrderListView.as_view(), name='list'),
    path('new/', views.order_form, name='create'),
    path('<int:pk>/', views.OrderDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.order_form, name='edit'),
    path('<int:pk>/status/', views.set_status, name='set_status'),
    path('<int:pk>/receive/', views.receive, name='receive'),
]
