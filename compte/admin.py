from django.contrib import admin
from compte.models import Compte, Total

class TotalAdmin(admin.ModelAdmin):
    list_display = ('no_total', 'desc')
    list_filter = ('desc',)

# Register your models here.
class CpmpteAdmin(admin.ModelAdmin):
    list_display = ('numero', 'libelle', 'no_total')
    list_filter = ('libelle',)

admin.site.register(Compte, CpmpteAdmin)
admin.site.register(Total, TotalAdmin)