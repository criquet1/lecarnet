from django.contrib import admin
from import_export.admin import ImportExportModelAdmin
from .models import Employe, FrequencePaie, Paie, ParametresTauxPaie, PeriodePaie


@admin.register(FrequencePaie)
class FrequencePaieAdmin(ImportExportModelAdmin, admin.ModelAdmin):
	list_display = ('code', 'nom', 'nombre_periodes_par_annee')


@admin.register(Employe)
class EmployeAdmin(ImportExportModelAdmin, admin.ModelAdmin):
	list_display = ('nom', 'prenom', 'date_embauche', 'frequence_paie', 'actif')
	list_filter = ('actif', 'frequence_paie')
	search_fields = ('nom', 'prenom')


@admin.register(PeriodePaie)
class PeriodePaieAdmin(ImportExportModelAdmin, admin.ModelAdmin):
	list_display = ('frequence_paie', 'date_debut', 'date_fin', 'date_paie', 'fermee')
	list_filter = ('frequence_paie', 'fermee')


@admin.register(Paie)
class PaieAdmin(ImportExportModelAdmin, admin.ModelAdmin):
	list_display = ('employe', 'periode', 'salaire_brut_periode', 'total_retenues', 'salaire_net')
	list_select_related = ('employe', 'periode', 'periode__frequence_paie')


@admin.register(ParametresTauxPaie)
class ParametresTauxPaieAdmin(ImportExportModelAdmin, admin.ModelAdmin):
	list_display = (
		'rrq_date_debut_effet',
		'rrq_date_fin_effet',
		'rqap_date_debut_effet',
		'rqap_date_fin_effet',
		'ae_date_debut_effet',
		'ae_date_fin_effet',
		'taux_rrq_employe',
		'taux_rrq_employeur',
		'taux_rqap_employe',
		'taux_rqap_employeur',
		'taux_ae_employe',
		'taux_ae_employeur',
	)
	ordering = ('-rrq_date_debut_effet', '-id')
