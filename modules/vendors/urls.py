from django.urls import path

from . import views

urlpatterns = [
    path('', views.VendorListView.as_view(), name='list'),
    path('new/', views.vendor_form, name='create'),
    path('<int:pk>/', views.VendorDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.vendor_form, name='edit'),
]
