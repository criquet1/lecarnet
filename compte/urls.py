from django.urls import path

from . import views

urlpatterns = [
    path('', views.compte_page, name='compte'),
    path('settings/', views.settings_page, name='settings'),
    path('creer-tenant/', views.creer_tenant_page, name='creer_tenant'),
    path('force-password-change/', views.force_password_change_page, name='force_password_change'),
    path('user-password-change/', views.user_password_change_page, name='user_password_change'),
    path('totaux/', views.totaux_page, name='totaux'),
    path('feuille-de-travail/', views.feuille_de_travail_page, name='feuille_de_travail'),
    path('transactions/', views.transactions_page, name='transactions'),
]
