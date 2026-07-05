from django.conf import settings
from django.db import models


class Societe(models.Model):
    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    adresse = models.CharField(max_length=255, blank=True)
    telephone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    personne_ressource = models.CharField(max_length=120, blank=True)
    site_web = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Societe'
        verbose_name_plural = 'Societes'

    def __str__(self):
        return self.name


class ClientDatabase(models.Model):
    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    db_alias = models.SlugField(max_length=50, unique=True)
    is_active = models.BooleanField(default=True)
    societe = models.ForeignKey(
        Societe,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='clients',
    )

    class Meta:
        ordering = ['name']
        verbose_name = 'Base client'
        verbose_name_plural = 'Bases clients'

    def __str__(self):
        return f"{self.name} ({self.db_alias})"


class UserClientAccess(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tenant_accesses',
    )
    client = models.ForeignKey(
        ClientDatabase,
        on_delete=models.CASCADE,
        related_name='user_accesses',
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        unique_together = [('user', 'client')]
        verbose_name = 'Acces utilisateur client'
        verbose_name_plural = 'Acces utilisateurs clients'

    def __str__(self):
        return f"{self.user} -> {self.client}"


class UserSocieteAccess(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='societe_accesses',
    )
    societe = models.ForeignKey(
        Societe,
        on_delete=models.CASCADE,
        related_name='user_accesses',
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        unique_together = [('user', 'societe')]
        verbose_name = 'Acces utilisateur societe'
        verbose_name_plural = 'Acces utilisateurs societes'

    def __str__(self):
        return f"{self.user} -> {self.societe}"


class UserSecurityState(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='security_state',
    )
    must_change_password = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Etat securite utilisateur'
        verbose_name_plural = 'Etats securite utilisateurs'

    def __str__(self):
        return f"{self.user} | must_change_password={self.must_change_password}"
