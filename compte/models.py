from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from decimal import Decimal
from paie.models import FrequencePaie

def validate_4_digits(value):
    if not (1000 <= value <= 9999):
        raise ValidationError("Le numéro doit être compris entre 1000 et 9999.")


def validate_total_number(value):
    if value == 0:
        return
    if not (1000 <= value <= 9999):
        raise ValidationError("Le total doit etre 0 ou compris entre 1000 et 9999.")

class Total(models.Model):
    no_total = models.IntegerField(
        primary_key=True,
        validators=[
            MinValueValidator(0), 
            MaxValueValidator(9999),
            validate_total_number
        ]
    )
    desc = models.CharField(max_length=120, blank=False, null=False)

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
    libelle = models.CharField(max_length=120, blank=False, null=False)
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


class Setting(models.Model):
    TAX_MODE_RECLAMER = 'RECLAMER'
    TAX_MODE_PAYER = 'PAYER'
    TAX_MODE_CHOICES = [
        (TAX_MODE_RECLAMER, 'Les taxes sont generalement a reclamer'),
        (TAX_MODE_PAYER, 'Les taxes sont generalement a payer'),
    ]

    nom = models.CharField(max_length=60, blank=False, null=False)
    logo = models.CharField(
        max_length=100,
        blank=False,
        null=False,
        default='images.png',
        help_text="Nom du fichier logo dans static/images/logos (ex: images.png)."
    )
    adresse = models.CharField(max_length=60, blank=False, null=False)
    ville = models.CharField(max_length=255, blank=False, null=False)
    code_postal = models.CharField(max_length=60, blank=False, null=False)
    pays = models.CharField(max_length=255, blank=False, null=False)
    phone = models.CharField(max_length=100, blank=False, null=False)
    email = models.EmailField(blank=False, null=False)
    fin_annee_jour = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
        verbose_name="Jour de fin d'exercice",
    )
    fin_annee_mois = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
        verbose_name="Mois de fin d'exercice",
    )

    # Ajout de related_name uniques pour éviter le conflit
    car = models.ForeignKey(Compte, on_delete=models.SET_NULL, null=True, blank=True, related_name='settings_car')
    cap = models.ForeignKey(Compte, on_delete=models.SET_NULL, null=True, blank=True, related_name='settings_cap')
    
    compte_tps_percue = models.ForeignKey(
        Compte, 
        on_delete=models.SET_NULL,
        null=True, 
        blank=True, 
        related_name='settings_tps_percue'
    )

    compte_tps_payee = models.ForeignKey(
        Compte, 
        on_delete=models.SET_NULL,
        null=True, 
        blank=True, 
        related_name='settings_tps_payee'
    )
    compte_tvq_percue = models.ForeignKey(
        Compte, 
        on_delete=models.SET_NULL,
        null=True, 
        blank=True, 
        related_name='settings_tvq_percue'
    )
    compte_tvq_payee = models.ForeignKey(
        Compte, 
        on_delete=models.SET_NULL,
        null=True, 
        blank=True, 
        related_name='settings_tvq_payee'
    )
    compte_fr_retard = models.ForeignKey(Compte, on_delete=models.SET_NULL, null=True, blank=True, related_name='settings_fr_retard')
    taxes_mode = models.CharField(
        max_length=16,
        choices=TAX_MODE_CHOICES,
        default=TAX_MODE_RECLAMER,
    )
    frequence_paie = models.ForeignKey(
        FrequencePaie,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='settings',
        verbose_name="Fréquence de paie"
    )
    date_debut_periode_paie_annee = models.DateField(
        blank=True,
        null=True,
        verbose_name="Date de debut de la premiere periode de l annee",
    )
    date_premier_paiement_paie_annee = models.DateField(
        blank=True,
        null=True,
        verbose_name="Date du premier paiement de l annee",
    )

    def __str__(self):
        return self.nom
