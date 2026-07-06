from django.urls import path

from . import views


urlpatterns = [
    path('select-client/', views.select_client, name='select_client'),
    path('set-active-client/', views.set_active_client, name='set_active_client'),
    path('societes/', views.manage_societes, name='manage_societes'),
    path('societe-users/', views.manage_societe_users, name='manage_societe_users'),
]
