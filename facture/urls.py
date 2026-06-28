from django.urls import path
from . import views


urlpatterns = [
    path('', views.index, name='accueil'),
    path('facture/', views.facture, name='facture'),
    path('releve-bancaire/', views.releve_bancaire, name='releve_bancaire'),
    path('journal-general/', views.journal_general, name='journal_general'),
    path('grand-livre/', views.grand_livre, name='grand_livre'),
    path('balance-de-verification/', views.balance_de_verification, name='balance_de_verification'),
    path('rapport-de-taxes/', views.rapport_de_taxes, name='rapport_de_taxes'),
]
