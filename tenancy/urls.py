from django.urls import path

from . import views


urlpatterns = [
    path('select-client/', views.select_client, name='select_client'),
    path('set-active-client/', views.set_active_client, name='set_active_client'),
]
