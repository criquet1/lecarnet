from django.contrib import admin

from .models import ClientDatabase, UserClientAccess


@admin.register(ClientDatabase)
class ClientDatabaseAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'db_alias', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'slug', 'db_alias')


@admin.register(UserClientAccess)
class UserClientAccessAdmin(admin.ModelAdmin):
    list_display = ('user', 'client', 'is_default')
    list_filter = ('is_default', 'client')
    search_fields = ('user__username', 'client__name', 'client__slug')
