from django.urls import path

from . import views

urlpatterns = [
    path('', views.compte_page, name='compte'),
    path('settings/', views.settings_page, name='settings'),
]
