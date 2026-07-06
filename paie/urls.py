from django.urls import path

from . import views

app_name = 'paie'

urlpatterns = [
	path('', views.paie_dashboard, name='paie_dashboard'),
	path('employes/', views.employes_page, name='paie_employes'),
	path('employes/<int:employe_id>/', views.employe_edit_page, name='paie_employe_edit'),
	path('employes/<int:employe_id>/desactiver/', views.employe_desactiver_page, name='paie_employe_desactiver'),
	path('calendrier/', views.calendrier_paie_page, name='paie_calendrier'),
	path('saisir/', views.saisir_paie_page, name='paie_saisir'),
	path('api/prochaine-periode/', views.prochaine_periode_employe_api, name='paie_api_prochaine_periode'),
	path('journal/', views.journal_paies_page, name='paie_journal'),
]
