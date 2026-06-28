from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from compte.models import Compte


class Source(models.Model):
    nom = models.CharField(max_length=15, blank=False, null=False)

    def __str__(self):
        return self.nom


class Compagnie(models.Model):
    MODE_CAP = 'CAP'
    MODE_CAR = 'CAR'
    MODE_AUTRE = 'AUTRE'
    MODE_CHOICES = [
        (MODE_CAP, 'CAP'),
        (MODE_CAR, 'CAR'),
        (MODE_AUTRE, 'Autre'),
    ]

    nom = models.CharField(max_length=60, blank=False, null=False)
    logo = models.CharField(
        max_length=100,
        blank=False,
        null=False,
        default='images.png',
        help_text="Nom du fichier logo dans static/images/logos (ex: images.png)."
    )
    comptes = models.ManyToManyField(Compte, related_name='compagnies', blank=True)
    cap_ou_car = models.CharField(
        max_length=10,
        choices=MODE_CHOICES,
        default=MODE_AUTRE,
        blank=True,
        null=True,
    )

    def __str__(self):
        return self.nom


class Tr_desc(models.Model):
    no_ej = models.CharField(max_length=10, blank=False, null=False)
    compagnie = models.ForeignKey(Compagnie, on_delete=models.CASCADE, related_name='tr_desc', blank=True, null=True)
    date = models.DateField()
    description = models.CharField(max_length=100, blank=True, null=True)
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name='tr_desc', blank=True, null=True)

    def __str__(self):
        return f"Facture {self.no_ej}"


class RapportTaxes(models.Model):
    annee = models.PositiveSmallIntegerField()
    mois = models.PositiveSmallIntegerField()
    cree_le = models.DateTimeField(auto_now_add=True)
    transmis_le = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-annee', '-mois', '-id']
        constraints = [
            models.CheckConstraint(
                condition=Q(mois__gte=1) & Q(mois__lte=12),
                name='rapport_taxes_mois_entre_1_et_12',
            ),
            models.UniqueConstraint(
                fields=['annee', 'mois'],
                name='rapport_taxes_unique_annee_mois',
            ),
        ]

    @property
    def est_transmis(self):
        return self.transmis_le is not None

    def clean(self):
        super().clean()
        if self.mois and (self.mois < 1 or self.mois > 12):
            raise ValidationError("Le mois doit etre compris entre 1 et 12.")

        if not self.pk:
            return

        previous = RapportTaxes.objects.filter(pk=self.pk).values(
            'annee',
            'mois',
            'transmis_le',
        ).first()
        if not previous or previous['transmis_le'] is None:
            return

        changed = (
            self.annee != previous['annee']
            or self.mois != previous['mois']
            or self.transmis_le != previous['transmis_le']
        )
        if changed:
            raise ValidationError("Ce rapport a deja ete transmis et ne peut plus etre modifie.")

    def transmettre(self):
        if self.est_transmis:
            raise ValidationError("Ce rapport de taxes a deja ete transmis.")
        self.transmis_le = timezone.now()
        self.save(update_fields=['transmis_le'])

    def __str__(self):
        return f"Rapport taxes {self.annee}-{self.mois:02d}"


class Tr_detail(models.Model):
    tr_desc = models.ForeignKey(Tr_desc, on_delete=models.CASCADE, related_name='details')
    compte = models.ForeignKey(Compte, on_delete=models.CASCADE)
    montant = models.DecimalField(max_digits=10, decimal_places=2)
    rapport_taxes = models.ForeignKey(
        RapportTaxes,
        on_delete=models.SET_NULL,
        related_name='details_taxes',
        blank=True,
        null=True,
    )

    def _tax_account_ids(self):
        settings_instance = Setting.objects.first()
        if not settings_instance:
            return set()
        return {
            account_id for account_id in [
                settings_instance.compte_tps_percue_id,
                settings_instance.compte_tps_payee_id,
                settings_instance.compte_tvq_percue_id,
                settings_instance.compte_tvq_payee_id,
            ] if account_id
        }

    def is_tax_line(self):
        if not self.compte_id:
            return False
        return self.compte_id in self._tax_account_ids()

    def clean(self):
        super().clean()

        if self.rapport_taxes_id and not self.is_tax_line():
            raise ValidationError("Seules les lignes de taxes peuvent etre rattachees a un rapport de taxes.")

        if self.rapport_taxes and self.rapport_taxes.est_transmis:
            raise ValidationError("Impossible de modifier une ligne rattachee a un rapport de taxes transmis.")

        if self.rapport_taxes and self.tr_desc_id and self.tr_desc and self.tr_desc.date:
            line_year = self.tr_desc.date.year
            line_month = self.tr_desc.date.month
            if line_year != self.rapport_taxes.annee or line_month != self.rapport_taxes.mois:
                raise ValidationError(
                    "La ligne de taxes doit appartenir au meme mois et a la meme annee que le rapport."
                )

        if not self.pk:
            return

        previous_rapport_id = Tr_detail.objects.filter(pk=self.pk).values_list('rapport_taxes_id', flat=True).first()
        if not previous_rapport_id:
            return

        previous_rapport = RapportTaxes.objects.filter(pk=previous_rapport_id).first()
        if previous_rapport and previous_rapport.est_transmis:
            raise ValidationError("Cette ligne appartient deja a un rapport de taxes transmis et est verrouillee.")

    def _auto_assign_open_report(self):
        if self.rapport_taxes_id:
            return
        if not self.is_tax_line():
            return
        if not self.tr_desc_id:
            return

        tr_date = getattr(self.tr_desc, 'date', None)
        if not tr_date:
            return

        matching_report = RapportTaxes.objects.filter(
            annee=tr_date.year,
            mois=tr_date.month,
        ).first()

        if not matching_report:
            matching_report = RapportTaxes.objects.create(
                annee=tr_date.year,
                mois=tr_date.month,
            )

        if matching_report and not matching_report.est_transmis:
            self.rapport_taxes = matching_report

    def save(self, *args, **kwargs):
        self._auto_assign_open_report()
        self.full_clean()
        return super().save(*args, **kwargs)


class Setting(models.Model):
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
    annee_financiere = models.DateField(blank=True, null=True)

    # Ajout de related_name uniques pour éviter le conflit
    car = models.ForeignKey(Compte, on_delete=models.SET_NULL, null=True, blank=True, related_name='settings_car')
    cap = models.ForeignKey(Compte, on_delete=models.SET_NULL, null=True, blank=True, related_name='settings_cap')
    
    compte_tps_percue = models.ForeignKey(
        Compte, 
        on_delete=models.SET_NULL, # Remplacez CASCADE par SET_NULL
        null=True, 
        blank=True, 
        related_name='settings_tps_percue'
    )

    compte_tps_payee = models.ForeignKey(
            Compte, 
            on_delete=models.SET_NULL, # Remplacez CASCADE par SET_NULL
            null=True, 
            blank=True, 
            related_name='settings_tps_payee'
        )
    compte_tvq_percue = models.ForeignKey(
            Compte, 
            on_delete=models.SET_NULL, # Remplacez CASCADE par SET_NULL
            null=True, 
            blank=True, 
            related_name='settings_tvq_percue'
        )
    compte_tvq_payee = models.ForeignKey(
        Compte, 
        on_delete=models.SET_NULL, # Remplacez CASCADE par SET_NULL
        null=True, 
        blank=True, 
        related_name='settings_tvq_payee'
    )
    compte_fr_retard = models.ForeignKey(Compte, on_delete=models.SET_NULL, null=True, blank=True, related_name='settings_fr_retard')

    def __str__(self):
        return self.nom
    

class CompteReleve(models.Model):
    TYPE_ONGLET_CHOICES = [
        ('banque', 'Banque'),
        ('carte_credit', 'Carte de crédit'),
        ('marge_credit', 'Marge de crédit'),
        ('autre', 'Autre'),
    ]

    no_compte = models.CharField(max_length=60)
    type_compte = models.CharField(max_length=20, blank=True)
    nom_affichage = models.CharField(max_length=60, blank=True)
    type_onglet = models.CharField(max_length=20, choices=TYPE_ONGLET_CHOICES, default='banque')
    nom_institut = models.CharField(max_length=60, blank=True)
    compte_comptable = models.ForeignKey(
        Compte,
        on_delete=models.SET_NULL,
        related_name='comptes_releves',
        blank=True,
        null=True,
    )

    class Meta:
        unique_together = ('no_compte', 'type_compte')

    def save(self, *args, **kwargs):
        if not self.nom_affichage:
            self.nom_affichage = f"{self.no_compte} {self.type_compte}".strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nom_affichage


class Releve(models.Model):
    compte_releve = models.ForeignKey(
        CompteReleve,
        on_delete=models.SET_NULL,
        related_name='releves',
        blank=True,
        null=True,
    )
    fichier_source = models.CharField(max_length=200, blank=False, null=False)
    nom_institut = models.CharField(max_length=60, blank=False, null=False)
    no_compte = models.CharField(max_length=60, blank=False, null=False)
    type_compte = models.CharField(max_length=10, blank=True, null=False, default='')
    date = models.DateField()
    no_ligne = models.CharField(max_length=30, blank=False, null=False)
    description = models.CharField(max_length=255, blank=False, null=False)
    frais = models.CharField(max_length=20, blank=True, null=False, default='')
    retrait = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    depot = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    champ1 = models.CharField(max_length=10, blank=True, null=True)
    champ2 = models.CharField(max_length=10, blank=True, null=True)
    champ3 = models.CharField(max_length=10, blank=True, null=True)
    champ4 = models.CharField(max_length=10, blank=True, null=True)
    solde = models.DecimalField(max_digits=10, decimal_places=2)
    ecriture_creee = models.BooleanField(default=False)
    ecriture_tr_desc = models.ForeignKey(
        Tr_desc,
        on_delete=models.SET_NULL,
        related_name='releves_sources',
        blank=True,
        null=True,
    )

    def __str__(self):
        return f"{self.no_compte} {self.type_compte} {self.date} #{self.no_ligne}"