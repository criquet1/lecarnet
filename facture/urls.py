from django.urls import path
from . import views


urlpatterns = [
    path('', views.index, name='accueil'),
    path('facture/', views.facture, name='facture'),
]
