from django.contrib import admin

from .models import ClientDatabase, Societe, UserClientAccess, UserSocieteAccess


@admin.register(Societe)
class SocieteAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'personne_ressource', 'telephone', 'email', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'slug', 'email', 'personne_ressource', 'telephone')


@admin.register(ClientDatabase)
class ClientDatabaseAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'db_alias', 'societe', 'is_active')
    list_filter = ('is_active', 'societe')
    search_fields = ('name', 'slug', 'db_alias')


@admin.register(UserClientAccess)
class UserClientAccessAdmin(admin.ModelAdmin):
    list_display = ('user', 'client', 'is_default')
    list_filter = ('is_default', 'client')
    search_fields = ('user__username', 'client__name', 'client__slug')


@admin.register(UserSocieteAccess)
class UserSocieteAccessAdmin(admin.ModelAdmin):
    list_display = ('user', 'societe', 'is_default')
    list_filter = ('is_default', 'societe')
    search_fields = ('user__username', 'societe__name', 'societe__slug')
