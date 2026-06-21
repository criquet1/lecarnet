from django.db import models
from compte.models import Compte

class Compagnie(models.Model):
    nom = models.CharField(max_length=60, blank=False, null=False)
    image = models.ImageField(max_length=100, default='default.png')
    comptes = models.ManyToManyField('Compte', related_name='compagnies', blank=True)


class Facture(models.Model):
    numero = models.IntegerField(primary_key=True)
    date = models.DateField()
    compagnie = models.ForeignKey(Compagnie, on_delete=models.CASCADE)
    montant = models.DecimalField(max_digits=10, decimal_places=2)


class DetailFacture(models.Model):
    facture = models.ForeignKey(Facture, on_delete=models.CASCADE, related_name='details')
    compte = models.ForeignKey(Compte, on_delete=models.CASCADE)
    montant = models.DecimalField(max_digits=10, decimal_places=2)