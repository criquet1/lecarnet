from django.db import models


class FrequencePaie(models.Model):
    HEBDOMADAIRE = 'HEBDO'
    AUX_2_SEMAINES = 'BIHEBDO'
    DEUX_FOIS_MOIS = '2MOIS'
    PAR_MOIS = 'MOIS'

    CHOICES = [
        (HEBDOMADAIRE, 'Hebdomadaire'),
        (AUX_2_SEMAINES, 'Aux 2 semaines'),
        (DEUX_FOIS_MOIS, '2 fois par mois'),
        (PAR_MOIS, 'Par mois'),
    ]

    nom = models.CharField(max_length=50)
    code = models.CharField(max_length=10, choices=CHOICES, unique=True, primary_key=True)
    nombre_periodes_par_annee = models.IntegerField()

    class Meta:
        verbose_name = "Fréquence de paie"
        verbose_name_plural = "Fréquences de paie"

    def __str__(self):
        return self.get_code_display()


class Employe(models.Model):
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)
    date_embauche = models.DateField()
    salH = models.CharField(max_length=40, blank=True, null=True)
    e_prov = models.IntegerField(blank=True, null=True)
    e_fed = models.CharField(max_length=40, blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['date_embauche', 'id'], name='paie_employe_date_id_idx'),
        ]

    def __str__(self):
        return f"{self.nom} {self.prenom}"