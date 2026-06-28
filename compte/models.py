from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator

def validate_4_digits(value):
    if not (1000 <= value <= 9999):
        raise ValidationError("Le numéro doit être compris entre 1000 et 9999.")

class Total(models.Model):
    no_total = models.IntegerField(
        primary_key=True,
        validators=[
            MinValueValidator(1000), 
            MaxValueValidator(9999),
            validate_4_digits
        ]
    )
    desc = models.CharField(max_length=30, blank=False, null=False)

    class Meta:
        verbose_name = "Total"
        verbose_name_plural = "Totaux"

    def __str__(self):
        # Retourne une chaîne de caractères, nécessaire pour les clés étrangères et l'admin Django
        return f"{self.no_total} - {self.desc}"


class Compte(models.Model):
    numero = models.IntegerField(
        primary_key=True,
        validators=[
            MinValueValidator(1000), 
            MaxValueValidator(9999),
            validate_4_digits
        ]
    )
    libelle = models.CharField(max_length=30, blank=False, null=False)
    no_total = models.ForeignKey(
        Total,
        on_delete=models.CASCADE,    # Supprime ce compte si le Total associé est supprimé
        db_column='no_total',        # Force le nom exact de la colonne dans la base de données
        verbose_name="Total"         # Nom affiché dans l'interface d'administration
    )

    class Meta:
        verbose_name = "Compte"
        verbose_name_plural = "Comptes"

    def __str__(self):
        # Retourne une chaîne de caractères au lieu d'un entier direct
        return f"{self.numero} - {self.libelle}"


class SoldeAuxLivres(models.Model):
    compte = models.OneToOneField(
        Compte,
        on_delete=models.CASCADE,
        related_name='solde_aux_livres',
    )
    solde_depart = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Solde aux livres"
        verbose_name_plural = "Soldes aux livres"
        ordering = ['compte__numero']

    def __str__(self):
        return f"{self.compte.numero} - {self.solde_depart}"
