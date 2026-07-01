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
    description = models.CharField(max_length=40, blank=True, null=True)
    note_de_credit = models.BooleanField(default=False)
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name='tr_desc', blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['date', 'id'], name='facture_trdesc_date_id_idx'),
        ]

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

    class Meta:
        indexes = [
            models.Index(fields=['compte', 'tr_desc', 'id'], name='facture_trd_compte_trd_id_idx'),
            models.Index(fields=['tr_desc', 'id'], name='facture_trd_trd_id_idx'),
        ]

    def _tenant_db_alias(self, db_alias=None):
        if db_alias:
            return db_alias
        if self._state.db:
            return self._state.db
        tr_desc = self._state.fields_cache.get('tr_desc') if hasattr(self._state, 'fields_cache') else None
        if tr_desc is not None and getattr(tr_desc._state, 'db', None):
            return tr_desc._state.db
        return None

    def _tax_account_ids(self, db_alias=None):
        db_alias = self._tenant_db_alias(db_alias)
        settings_qs = Setting.objects.using(db_alias) if db_alias else Setting.objects
        settings_instance = settings_qs.first()
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

    def is_tax_line(self, db_alias=None):
        if not self.compte_id:
            return False
        return self.compte_id in self._tax_account_ids(db_alias=db_alias)

    def clean(self):
        super().clean()

        db_alias = self._tenant_db_alias()
        rapport_qs = RapportTaxes.objects.using(db_alias) if db_alias else RapportTaxes.objects
        detail_qs = Tr_detail.objects.using(db_alias) if db_alias else Tr_detail.objects

        if self.rapport_taxes_id and not self.is_tax_line(db_alias=db_alias):
            raise ValidationError("Seules les lignes de taxes peuvent etre rattachees a un rapport de taxes.")

        if self.rapport_taxes and self.rapport_taxes.est_transmis:
            raise ValidationError("Impossible de modifier une ligne rattachee a un rapport de taxes transmis.")

        if self.rapport_taxes and self.tr_desc_id and self.tr_desc and self.tr_desc.date:
            line_year = self.tr_desc.date.year
            line_month = self.tr_desc.date.month
            report_period = (self.rapport_taxes.annee, self.rapport_taxes.mois)
            line_period = (line_year, line_month)
            if report_period < line_period:
                raise ValidationError(
                    "La ligne de taxes doit etre rattachee a un rapport du meme mois ou d'un mois ulterieur."
                )

        if not self.pk:
            return

        previous_rapport_id = detail_qs.filter(pk=self.pk).values_list('rapport_taxes_id', flat=True).first()
        if not previous_rapport_id:
            return

        previous_rapport = rapport_qs.filter(pk=previous_rapport_id).first()
        if previous_rapport and previous_rapport.est_transmis:
            raise ValidationError("Cette ligne appartient deja a un rapport de taxes transmis et est verrouillee.")

    def _auto_assign_open_report(self, db_alias=None):
        if self.rapport_taxes_id:
            return
        db_alias = self._tenant_db_alias(db_alias)
        if not self.is_tax_line(db_alias=db_alias):
            return
        if not self.tr_desc_id:
            return

        # Ne pas rattacher les ecritures de transmission du rapport de taxes
        # au rapport mensuel source; elles doivent rester hors du bloc de calcul.
        if self.tr_desc and self.tr_desc.source and self.tr_desc.source.nom == 'Rapport de taxes':
            return

        tr_date = getattr(self.tr_desc, 'date', None)
        if not tr_date:
            return

        rapport_qs = RapportTaxes.objects.using(db_alias) if db_alias else RapportTaxes.objects

        matching_report = rapport_qs.filter(
            annee=tr_date.year,
            mois=tr_date.month,
        ).first()

        if not matching_report:
            matching_report = rapport_qs.create(
                annee=tr_date.year,
                mois=tr_date.month,
            )

        if matching_report and not matching_report.est_transmis:
            self.rapport_taxes = matching_report
            return

        # Rattrapage: si le mois d'origine est transmis, rattacher au premier
        # rapport non transmis qui suit ce mois (ex: mai transmis -> juin brouillon).
        next_open_report = rapport_qs.filter(
            transmis_le__isnull=True,
        ).filter(
            Q(annee__gt=tr_date.year) |
            (Q(annee=tr_date.year) & Q(mois__gt=tr_date.month))
        ).order_by('annee', 'mois').first()
        if next_open_report:
            self.rapport_taxes = next_open_report
            return

        year = tr_date.year
        month = tr_date.month
        # Cree le prochain mois ouvert s'il n'existe pas deja.
        for _ in range(120):
            month += 1
            if month > 12:
                month = 1
                year += 1

            candidate, _ = rapport_qs.get_or_create(
                annee=year,
                mois=month,
            )
            if not candidate.est_transmis:
                self.rapport_taxes = candidate
                return

    def save(self, *args, **kwargs):
        db_alias = kwargs.get('using')
        if db_alias and not self._state.db:
            self._state.db = db_alias

        tenant_alias = self._tenant_db_alias(db_alias)

        if self.tr_desc_id and tenant_alias:
            if not Tr_desc.objects.using(tenant_alias).filter(pk=self.tr_desc_id).exists():
                raise ValidationError({'tr_desc': "Facture introuvable sur la base client active."})

        if self.compte_id and tenant_alias:
            if not Compte.objects.using(tenant_alias).filter(pk=self.compte_id).exists():
                raise ValidationError({'compte': "Compte introuvable sur la base client active."})

        if self.rapport_taxes_id and tenant_alias:
            if not RapportTaxes.objects.using(tenant_alias).filter(pk=self.rapport_taxes_id).exists():
                raise ValidationError({'rapport_taxes': "Rapport de taxes introuvable sur la base client active."})

        self._auto_assign_open_report(db_alias=db_alias)
        self.full_clean(exclude=['tr_desc', 'compte', 'rapport_taxes'])
        return super().save(*args, **kwargs)


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
    taxes_mode = models.CharField(
        max_length=16,
        choices=TAX_MODE_CHOICES,
        default=TAX_MODE_RECLAMER,
    )

    def __str__(self):
        return self.nom


class CompagnieSoldeDepart(models.Model):
    compagnie = models.OneToOneField(
        Compagnie,
        on_delete=models.CASCADE,
        related_name='solde_depart',
    )
    montant = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name = 'Solde de depart compagnie'
        verbose_name_plural = 'Soldes de depart compagnies'
        ordering = ['compagnie__nom']

    def __str__(self):
        return f"{self.compagnie.nom} - {self.montant}"
    

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
    desc_releve = models.CharField(max_length=255, blank=False, null=False)
    desc_ctb = models.CharField(max_length=40, blank=True, null=False, default='')
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