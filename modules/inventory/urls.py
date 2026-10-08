from django.urls import path

from . import views

urlpatterns = [
    path('', views.PartListView.as_view(), name='part_list'),
    path('parts/new/', views.PartCreateView.as_view(), name='part_create'),
    path('parts/search/', views.part_search, name='part_search'),
    path('parts/quick/', views.part_quick_create, name='part_quick_create'),
    path('parts/<int:pk>/', views.PartDetailView.as_view(), name='part_detail'),
    path('parts/<int:pk>/edit/', views.PartUpdateView.as_view(), name='part_edit'),
    path('parts/lookup/', views.part_lookup, name='part_lookup'),
    path('families/', views.FamilyListView.as_view(), name='family_list'),
    path('families/new/', views.FamilyCreateView.as_view(), name='family_create'),
    path('families/<int:pk>/', views.FamilyDetailView.as_view(), name='family_detail'),
    path('families/<int:pk>/edit/', views.FamilyUpdateView.as_view(), name='family_edit'),
    path('families/<int:pk>/delete/', views.FamilyDeleteView.as_view(), name='family_delete'),
    path('categories/', views.CategoryListView.as_view(), name='category_list'),
    path('categories/new/', views.category_form, name='category_create'),
    path('categories/<int:pk>/', views.category_form, name='category_edit'),
    path('categories/<int:pk>/attributes/add/', views.category_add_attributes, name='category_add_attributes'),
]
