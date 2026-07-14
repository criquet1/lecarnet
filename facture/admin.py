from django.contrib import admin
from facture.models import Compagnie, Tr_desc, Tr_detail, Releve, RapportTaxes, CompteReleve, CompagnieSoldeDepart
from compte.models import Setting
from import_export.admin import ImportExportModelAdmin


class SettingAdmin(ImportExportModelAdmin, admin.ModelAdmin):
    list_display = ('id', 'nom', 'email', 'phone', 'adresse', 'ville', 'code_postal', 'pays')
    list_filter = ('nom',)


class CompagnieAdmin(ImportExportModelAdmin,admin.ModelAdmin):
    list_display = ('id', 'nom', 'logo', 'get_cap_ou_car', 'get_comptes')
    list_filter = ('nom',)
    list_editable = ('nom',)

    def get_cap_ou_car(self, obj):
        return obj.cap_ou_car or ''

    get_cap_ou_car.short_description = 'CAP/CAR'
    
    def get_comptes(self, obj):
        return ", ".join([compte.libelle for compte in obj.comptes.all()])
    
    get_comptes.short_description = 'Comptes'

class TrDescAdmin(ImportExportModelAdmin, admin.ModelAdmin):
    list_display = ('id', 'no_ej', 'compagnie', 'date', 'transaction_releve', 'desc_ctb', 'source')
    list_filter = ('date', 'compagnie')
    search_fields = ('desc_releve', 'desc_ctb')
    ordering = ('-date',)

    def transaction_releve(self, obj):
        return obj.desc_releve or ''

    transaction_releve.short_description = 'Transaction relevé'


class TrDetailAdmin(ImportExportModelAdmin, admin.ModelAdmin):
    list_display = ('id', 'tr_desc', 'compte', 'montant', 'rapport_taxes')
    list_filter = ('compte', 'tr_desc__compagnie')
    search_fields = ('tr_desc__no_ej', 'compte__libelle')
    ordering = ('-id',)


class RapportTaxesAdmin(ImportExportModelAdmin, admin.ModelAdmin):
    list_display = ('id', 'annee', 'mois', 'cree_le', 'transmis_le')
    list_filter = ('transmis_le',)
    ordering = ('-annee', '-mois', '-id')

class ReleveBancaireAdmin(ImportExportModelAdmin, admin.ModelAdmin):
    list_display = ('id', 'fichier_source', 'nom_institut', 'no_compte', 'type_compte', 'date', 'no_ligne', 'desc_releve', 'desc_ctb')
    list_filter = ('nom_institut', 'date')
    search_fields = ('nom_institut', 'desc_releve', 'desc_ctb')


class CompteReleveAdmin(ImportExportModelAdmin, admin.ModelAdmin):
    list_display = ('id', 'nom_affichage', 'no_compte', 'type_compte', 'type_onglet', 'compte_comptable')
    list_filter = ('type_onglet', 'type_compte')
    search_fields = ('nom_affichage', 'no_compte', 'nom_institut', 'compte_comptable__libelle')


admin.site.register(Compagnie, CompagnieAdmin)
admin.site.register(Setting, SettingAdmin)
admin.site.register(Tr_desc, TrDescAdmin)
admin.site.register(Tr_detail, TrDetailAdmin)
admin.site.register(Releve, ReleveBancaireAdmin)
admin.site.register(RapportTaxes, RapportTaxesAdmin)
admin.site.register(CompteReleve, CompteReleveAdmin)
admin.site.register(CompagnieSoldeDepart)