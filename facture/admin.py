from django.contrib import admin
from facture.models import Compagnie, Facture, DetailFacture, Setting
from compte.models import Compte


class CompagnieAdmin(admin.ModelAdmin):
    list_display = ('id', 'nom', 'logo', 'get_comptes') # Replaced 'comptes' with method
    list_filter = ('nom',)
    list_editable = ('nom',)
    
    def get_comptes(self, obj):
        # Joins all related Compte names into a single comma-separated string
        return ", ".join([compte.libelle for compte in obj.comptes.all()])
    
    get_comptes.short_description = 'Comptes' # Customizes column header name


class FactureAdmin(admin.ModelAdmin):
    list_display = ('id', 'numero', 'date', 'compagnie', 'total')
    list_filter = ('compagnie',)


class DétailFactureAdmin(admin.ModelAdmin):
    list_display = ('id', 'facture', 'compte', 'montant')
    list_filter = ('facture',)


class SettingAdmin(admin.ModelAdmin):
    list_display = ('id', 'nom', 'email', 'phone', 'adresse', 'ville', 'code_postal', 'pays')
    list_filter = ('nom',)


admin.site.register(Compagnie, CompagnieAdmin)
admin.site.register(Facture, FactureAdmin) 
admin.site.register(DetailFacture, DétailFactureAdmin)
admin.site.register(Setting, SettingAdmin)