from django.contrib import admin
from compte.models import Compte, ExerciceFinancier, SoldeAuxLivres, Total
from import_export.admin import ImportExportModelAdmin

class TotalAdmin(ImportExportModelAdmin, admin.ModelAdmin):
    list_display = ('no_total', 'desc')
    list_filter = ('desc',)

# Register your models here.
class CompteAdmin(ImportExportModelAdmin, admin.ModelAdmin):
    list_display = ('numero', 'libelle', 'no_total')
    list_filter = ('libelle',)


class SoldeAuxLivresAdmin(ImportExportModelAdmin, admin.ModelAdmin):
    list_display = ('compte', 'solde_depart')
    list_filter = ('compte',)

class ExerciceFinancierAdmin(ImportExportModelAdmin, admin.ModelAdmin):
    list_display = ('date_debut', 'date_fin', 'est_audite', 'cloture_le')
    list_filter = ('est_audite',)


admin.site.register(Compte, CompteAdmin)
admin.site.register(Total, TotalAdmin)
admin.site.register(SoldeAuxLivres, SoldeAuxLivresAdmin)
admin.site.register(ExerciceFinancier, ExerciceFinancierAdmin)