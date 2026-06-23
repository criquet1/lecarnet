from django.db import models
from compte.models import Compte

class Compagnie(models.Model):
    nom = models.CharField(max_length=60, blank=False, null=False)
    logo = models.CharField(
        max_length=100,
        default='images/logos/images.png',
        help_text="Chemin relatif dans static/ vers le logo (ex: images/logos/Hydro-Québec-Logo.png)."
    )
    comptes = models.ManyToManyField(Compte, related_name='compagnies', blank=True)
    cap_ou_car = models.ForeignKey(
        Compte,
        on_delete=models.CASCADE,
        related_name='compagnies_cap_ou_car',
        blank=True,
        null=True,
    )

    def __str__(self):
        return self.nom


class Facture(models.Model):
    numero = models.CharField(max_length=30, blank=False, null=False)
    date = models.DateField()
    compagnie = models.ForeignKey(Compagnie, on_delete=models.CASCADE)
    total = models.DecimalField(max_digits=10, decimal_places=2)


class DetailFacture(models.Model):
    facture = models.ForeignKey(Facture, on_delete=models.CASCADE, related_name='details')
    compte = models.ForeignKey(Compte, on_delete=models.CASCADE)
    montant = models.DecimalField(max_digits=10, decimal_places=2)


class Setting(models.Model):
    nom = models.CharField(max_length=60, blank=False, null=False)
    logo = models.CharField(
        max_length=100,
        blank=False,
        null=False,
        default='images/logos/images.png',
        help_text="Chemin relatif dans static/ vers le logo (ex: images/logos/votre-logo.png)."
    )
    adresse = models.CharField(max_length=60, blank=False, null=False)
    ville = models.CharField(max_length=255, blank=False, null=False)
    code_postal = models.CharField(max_length=60, blank=False, null=False)
    pays = models.CharField(max_length=255, blank=False, null=False)
    phone = models.CharField(max_length=100, blank=False, null=False)
    email = models.EmailField(blank=False, null=False)

    # Ajout de related_name uniques pour éviter le conflit
    compte_tps = models.ForeignKey(Compte, on_delete=models.CASCADE, related_name='settings_tps')
    compte_tvq = models.ForeignKey(Compte, on_delete=models.CASCADE, related_name='settings_tvq')
    compte_fr_retard = models.ForeignKey(Compte, on_delete=models.CASCADE, related_name='settings_fr_retard')

    def __str__(self):
        return self.nom