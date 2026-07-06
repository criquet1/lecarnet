from django.contrib import admin

from .models import Employe, FrequencePaie, Paie, PeriodePaie


@admin.register(FrequencePaie)
class FrequencePaieAdmin(admin.ModelAdmin):
	list_display = ('code', 'nom', 'nombre_periodes_par_annee')


@admin.register(Employe)
class EmployeAdmin(admin.ModelAdmin):
	list_display = ('nom', 'prenom', 'date_embauche', 'frequence_paie', 'actif')
	list_filter = ('actif', 'frequence_paie')
	search_fields = ('nom', 'prenom')


@admin.register(PeriodePaie)
class PeriodePaieAdmin(admin.ModelAdmin):
	list_display = ('frequence_paie', 'date_debut', 'date_fin', 'date_paie', 'fermee')
	list_filter = ('frequence_paie', 'fermee')


@admin.register(Paie)
class PaieAdmin(admin.ModelAdmin):
	list_display = ('employe', 'periode', 'salaire_brut_periode', 'total_retenues', 'salaire_net')
	list_select_related = ('employe', 'periode', 'periode__frequence_paie')
