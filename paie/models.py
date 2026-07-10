from datetime import date as date_type
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum

from paie.services.das import DASInputs, calculer_das


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
        verbose_name = 'Fréquence de paie'
        verbose_name_plural = 'Fréquences de paie'

    def __str__(self):
        return self.get_code_display()


class Employe(models.Model):
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)
    date_embauche = models.DateField()
    salH = models.CharField(max_length=40, blank=True, null=True)
    e_prov = models.IntegerField(blank=True, null=True)
    e_fed = models.CharField(max_length=40, blank=True, null=True)
    taux_vacances = models.DecimalField(
        max_digits=7,
        decimal_places=5,
        blank=True,
        null=True,
        default=Decimal('0.00000'),
    )
    frequence_paie = models.ForeignKey(
        FrequencePaie,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='employes',
    )
    actif = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=['date_embauche', 'id'], name='paie_employe_date_id_idx'),
        ]
        ordering = ['nom', 'prenom', 'id']

    def _decimal_or_zero(self, valeur):
        if valeur in (None, ''):
            return Decimal('0.00')
        try:
            return Decimal(str(valeur))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal('0.00')

    @property
    def taux_horaire_defaut(self):
        return self._decimal_or_zero(self.salH)

    @property
    def montant_personnel_quebec_defaut(self):
        return self._decimal_or_zero(self.e_prov)

    @property
    def montant_personnel_federal_defaut(self):
        return self._decimal_or_zero(self.e_fed)

    def __str__(self):
        return f'{self.nom} {self.prenom}'


class PeriodePaie(models.Model):
    frequence_paie = models.ForeignKey(
        FrequencePaie,
        on_delete=models.PROTECT,
        related_name='periodes',
    )
    date_debut = models.DateField(blank=True, null=True)
    date_fin = models.DateField()
    date_paie = models.DateField(blank=True, null=True)
    fermee = models.BooleanField(default=False)

    class Meta:
        ordering = ['-date_fin', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['frequence_paie', 'date_debut', 'date_fin'],
                name='paie_periode_unique_frequence_dates',
            ),
        ]

    @property
    def nombre_periodes_par_annee(self):
        return self.frequence_paie.nombre_periodes_par_annee

    def clean(self):
        super().clean()
        if self.date_debut and self.date_fin and self.date_debut > self.date_fin:
            raise ValidationError('La date de debut doit preceder la date de fin.')

    def save(self, *args, **kwargs):
        if self.date_paie is None:
            self.date_paie = self.date_fin
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.frequence_paie} - {self.date_fin:%Y-%m-%d}'


class ParametresTauxPaie(models.Model):
    rrq_date_debut_effet = models.DateField()
    rrq_date_fin_effet = models.DateField(blank=True, null=True)

    taux_rrq_employe = models.DecimalField(max_digits=7, decimal_places=5)
    taux_rrq_supplementaire_2_employe = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('4.00000'))
    taux_rrq_employeur = models.DecimalField(max_digits=7, decimal_places=5)
    exemption_base_rrq = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('3500.00'))
    max_assurable_rrq = models.DecimalField(max_digits=12, decimal_places=2)
    max_supplementaire_rrq = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('85000.00'))

    rqap_date_debut_effet = models.DateField()
    rqap_date_fin_effet = models.DateField(blank=True, null=True)

    taux_rqap_employe = models.DecimalField(max_digits=7, decimal_places=5)
    taux_rqap_employeur = models.DecimalField(max_digits=7, decimal_places=5)
    max_assurable_rqap = models.DecimalField(max_digits=12, decimal_places=2)

    ae_date_debut_effet = models.DateField()
    ae_date_fin_effet = models.DateField(blank=True, null=True)

    taux_ae_employe = models.DecimalField(max_digits=7, decimal_places=5)
    taux_ae_employeur = models.DecimalField(max_digits=7, decimal_places=5)
    taux_cnt_employeur = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('0.06000'))
    max_assurable_ae = models.DecimalField(max_digits=12, decimal_places=2)

    credit_personnel_federal_min = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('16452.00'))
    taux_credit_federal = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('14.00000'))
    montant_canadien_pour_emploi = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1501.00'))
    abattement_federal_quebec = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('16.50000'))
    seuil_federal_1 = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('58523.00'))
    seuil_federal_2 = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('117045.00'))
    seuil_federal_3 = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('181440.00'))
    seuil_federal_4 = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('258482.00'))
    taux_federal_1 = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('14.00000'))
    taux_federal_2 = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('20.50000'))
    taux_federal_3 = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('26.00000'))
    taux_federal_4 = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('29.00000'))
    taux_federal_5 = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('33.00000'))

    credit_personnel_quebec_min = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('18952.00'))
    deduction_travailleur_qc_max_annuelle = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1450.00'))
    seuil_qc_1 = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('54345.00'))
    seuil_qc_2 = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('108680.00'))
    seuil_qc_3 = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('132245.00'))
    taux_qc_1 = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('14.00000'))
    taux_qc_2 = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('19.00000'))
    taux_qc_3 = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('24.00000'))
    taux_qc_4 = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('25.75000'))
    taux_credit_quebec = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('14.00000'))

    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-rrq_date_debut_effet', '-id']
        verbose_name = 'Parametres de taux de paie'
        verbose_name_plural = 'Parametres de taux de paie'

    def _validate_block_period(self, overlap_qs, start_field, end_field, block_label):
        start_date = getattr(self, start_field)
        end_date = getattr(self, end_field)

        if end_date and end_date < start_date:
            raise ValidationError(f'La date de fin doit etre superieure ou egale a la date de debut pour {block_label}.')

        this_end = end_date or date_type.max
        for row in overlap_qs:
            row_start = getattr(row, start_field)
            row_end = getattr(row, end_field) or date_type.max
            if row_start <= this_end and start_date <= row_end:
                raise ValidationError(f'La periode d effet {block_label} chevauche une autre configuration de taux.')

    def clean(self):
        super().clean()
        overlap_qs = self.__class__.objects.all()
        if self.pk:
            overlap_qs = overlap_qs.exclude(pk=self.pk)

        self._validate_block_period(overlap_qs, 'rrq_date_debut_effet', 'rrq_date_fin_effet', 'RRQ')
        self._validate_block_period(overlap_qs, 'rqap_date_debut_effet', 'rqap_date_fin_effet', 'RQAP')
        self._validate_block_period(overlap_qs, 'ae_date_debut_effet', 'ae_date_fin_effet', 'AE')

    def __str__(self):
        return f'Taux paie RRQ {self.rrq_date_debut_effet.isoformat()}'


class Paie(models.Model):
    employe = models.ForeignKey(Employe, on_delete=models.PROTECT, related_name='paies')
    periode = models.ForeignKey(PeriodePaie, on_delete=models.PROTECT, related_name='paies')
    heures_travaillees = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('0.00'))
    vacances_payees = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    vacances = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    taux_horaire = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    montant_personnel_federal_td1 = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    montant_personnel_quebec_tp1015 = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    deduction_code_f = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    deduction_tp1015_j = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    deduction_tp1016_j1 = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    retenue_supplementaire_qc = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    cotisation_supplementaire_rrq_csa = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    salaire_brut_periode = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    rqap = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    rrq = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    ae = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    impot_federal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    impot_provincial = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total_retenues = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    salaire_net = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-periode__date_fin', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['employe', 'periode'],
                name='paie_unique_employe_periode',
            ),
        ]

    CALC_INPUT_FIELDS = (
        'employe_id',
        'periode_id',
        'heures_travaillees',
        'vacances_payees',
        'vacances',
        'taux_horaire',
        'montant_personnel_federal_td1',
        'montant_personnel_quebec_tp1015',
        'deduction_code_f',
        'deduction_tp1015_j',
        'deduction_tp1016_j1',
        'retenue_supplementaire_qc',
        'cotisation_supplementaire_rrq_csa',
    )

    def clean(self):
        super().clean()
        if self.periode_id and self.employe.frequence_paie_id and self.periode.frequence_paie_id != self.employe.frequence_paie_id:
            raise ValidationError('La frequence de la periode doit correspondre a la frequence de l employe.')
        if self.periode_id and self.periode.fermee and self.pk is None:
            raise ValidationError('Impossible de creer une paie dans une periode fermee.')

    def _decimal_or_zero(self, valeur):
        return Decimal(str(valeur)) if valeur not in (None, '') else Decimal('0.00')

    def _date_value(self, valeur):
        if isinstance(valeur, date_type):
            return valeur
        if isinstance(valeur, str):
            return date_type.fromisoformat(valeur)
        raise ValidationError('La periode doit fournir une date de fin valide.')

    def _cumuls_precedents(self):
        if not self.periode_id:
            return {
                'salaire_brut_periode__sum': Decimal('0.00'),
                'rrq__sum': Decimal('0.00'),
                'rqap__sum': Decimal('0.00'),
                'ae__sum': Decimal('0.00'),
            }

        date_paie = self._date_value(self.periode.date_paie or self.periode.date_fin)
        date_fin = self._date_value(self.periode.date_fin)

        qs = self.__class__.objects.filter(
            employe=self.employe,
            periode__date_paie__year=date_paie.year,
            periode__date_paie__lt=date_paie,
        )
        # Si plusieurs paies partagent la meme date de paiement, on garde
        # l'ordre chronologique par date de fin pour calculer les cumuls.
        qs = qs | self.__class__.objects.filter(
            employe=self.employe,
            periode__date_paie=date_paie,
            periode__date_fin__lt=date_fin,
        )
        if self.pk:
            qs = qs.exclude(pk=self.pk)

        return qs.aggregate(
            Sum('salaire_brut_periode'),
            Sum('rrq'),
            Sum('rqap'),
            Sum('ae'),
        )

    def _nombre_periodes_par_annee(self):
        if self.periode_id and self.periode.frequence_paie_id:
            return self.periode.frequence_paie.nombre_periodes_par_annee
        if self.employe.frequence_paie_id:
            return self.employe.frequence_paie.nombre_periodes_par_annee
        raise ValidationError('Une frequence de paie est requise sur la periode ou l employe.')

    @staticmethod
    def _percent_to_ratio(value, fallback_percent):
        if value in (None, ''):
            return Decimal(str(fallback_percent)) / Decimal('100')
        return Decimal(str(value)) / Decimal('100')

    @staticmethod
    def _taux_effectifs(date_reference):
        rows = list(ParametresTauxPaie.objects.using('default').all())
        fallback_taux_rrq_base = Decimal('6.30000')
        fallback_taux_rrq_supp_2 = Decimal('4.00000')
        fallback_taux_rqap = Decimal('0.43000')
        fallback_taux_ae = Decimal('1.30000')
        fallback_taux_rqap_ratio = Decimal('0.00430')
        fallback_taux_ae_ratio = Decimal('0.0130')
        fallback_max_assurable_rrq = Decimal('74600.00')
        fallback_max_supplementaire_rrq = Decimal('85000.00')
        fallback_max_assurable_rqap = Decimal('424.31') / fallback_taux_rqap_ratio
        fallback_max_assurable_ae = Decimal('878.22') / fallback_taux_ae_ratio
        fallback_credit_personnel_federal_min = Decimal('16452.00')
        fallback_taux_credit_federal = Decimal('14.00000')
        fallback_montant_canadien_pour_emploi = Decimal('1501.00')
        fallback_abattement_federal_quebec = Decimal('16.50000')
        fallback_seuil_federal_1 = Decimal('58523.00')
        fallback_seuil_federal_2 = Decimal('117045.00')
        fallback_seuil_federal_3 = Decimal('181440.00')
        fallback_seuil_federal_4 = Decimal('258482.00')
        fallback_taux_federal_1 = Decimal('14.00000')
        fallback_taux_federal_2 = Decimal('20.50000')
        fallback_taux_federal_3 = Decimal('26.00000')
        fallback_taux_federal_4 = Decimal('29.00000')
        fallback_taux_federal_5 = Decimal('33.00000')
        fallback_credit_personnel_quebec_min = Decimal('18952.00')
        fallback_deduction_travailleur_qc_max_annuelle = Decimal('1450.00')
        fallback_seuil_qc_1 = Decimal('54345.00')
        fallback_seuil_qc_2 = Decimal('108680.00')
        fallback_seuil_qc_3 = Decimal('132245.00')
        fallback_taux_qc_1 = Decimal('14.00000')
        fallback_taux_qc_2 = Decimal('19.00000')
        fallback_taux_qc_3 = Decimal('24.00000')
        fallback_taux_qc_4 = Decimal('25.75000')
        fallback_taux_credit_quebec = Decimal('14.00000')

        def _pick(start_field, end_field):
            candidates = [
                row for row in rows
                if getattr(row, start_field) <= date_reference and (getattr(row, end_field) is None or getattr(row, end_field) >= date_reference)
            ]
            if not candidates:
                return None
            return sorted(candidates, key=lambda row: (getattr(row, start_field), row.id), reverse=True)[0]

        rrq_row = _pick('rrq_date_debut_effet', 'rrq_date_fin_effet')
        rqap_row = _pick('rqap_date_debut_effet', 'rqap_date_fin_effet')
        ae_row = _pick('ae_date_debut_effet', 'ae_date_fin_effet')
        fiscal_row = rrq_row or rqap_row or ae_row

        return {
            'taux_rrq_employe': Paie._percent_to_ratio(getattr(rrq_row, 'taux_rrq_employe', None) if rrq_row else None, str(fallback_taux_rrq_base)),
            'taux_rrq_supplementaire_2_employe': Paie._percent_to_ratio(getattr(rrq_row, 'taux_rrq_supplementaire_2_employe', None) if rrq_row else None, str(fallback_taux_rrq_supp_2)),
            'exemption_base_rrq': getattr(rrq_row, 'exemption_base_rrq', Decimal('3500.00')) if rrq_row else Decimal('3500.00'),
            'max_assurable_rrq': getattr(rrq_row, 'max_assurable_rrq', fallback_max_assurable_rrq) if rrq_row else fallback_max_assurable_rrq,
            'max_supplementaire_rrq': getattr(rrq_row, 'max_supplementaire_rrq', fallback_max_supplementaire_rrq) if rrq_row else fallback_max_supplementaire_rrq,
            'taux_rqap_employe': Paie._percent_to_ratio(getattr(rqap_row, 'taux_rqap_employe', None) if rqap_row else None, str(fallback_taux_rqap)),
            'max_assurable_rqap': getattr(rqap_row, 'max_assurable_rqap', fallback_max_assurable_rqap) if rqap_row else fallback_max_assurable_rqap,
            'taux_ae_employe': Paie._percent_to_ratio(getattr(ae_row, 'taux_ae_employe', None) if ae_row else None, str(fallback_taux_ae)),
            'max_assurable_ae': getattr(ae_row, 'max_assurable_ae', fallback_max_assurable_ae) if ae_row else fallback_max_assurable_ae,
            'credit_personnel_federal_min': getattr(fiscal_row, 'credit_personnel_federal_min', fallback_credit_personnel_federal_min) if fiscal_row else fallback_credit_personnel_federal_min,
            'taux_credit_federal': Paie._percent_to_ratio(getattr(fiscal_row, 'taux_credit_federal', None) if fiscal_row else None, str(fallback_taux_credit_federal)),
            'montant_canadien_pour_emploi': getattr(fiscal_row, 'montant_canadien_pour_emploi', fallback_montant_canadien_pour_emploi) if fiscal_row else fallback_montant_canadien_pour_emploi,
            'abattement_federal_quebec': Paie._percent_to_ratio(getattr(fiscal_row, 'abattement_federal_quebec', None) if fiscal_row else None, str(fallback_abattement_federal_quebec)),
            'seuil_federal_1': getattr(fiscal_row, 'seuil_federal_1', fallback_seuil_federal_1) if fiscal_row else fallback_seuil_federal_1,
            'seuil_federal_2': getattr(fiscal_row, 'seuil_federal_2', fallback_seuil_federal_2) if fiscal_row else fallback_seuil_federal_2,
            'seuil_federal_3': getattr(fiscal_row, 'seuil_federal_3', fallback_seuil_federal_3) if fiscal_row else fallback_seuil_federal_3,
            'seuil_federal_4': getattr(fiscal_row, 'seuil_federal_4', fallback_seuil_federal_4) if fiscal_row else fallback_seuil_federal_4,
            'taux_federal_1': Paie._percent_to_ratio(getattr(fiscal_row, 'taux_federal_1', None) if fiscal_row else None, str(fallback_taux_federal_1)),
            'taux_federal_2': Paie._percent_to_ratio(getattr(fiscal_row, 'taux_federal_2', None) if fiscal_row else None, str(fallback_taux_federal_2)),
            'taux_federal_3': Paie._percent_to_ratio(getattr(fiscal_row, 'taux_federal_3', None) if fiscal_row else None, str(fallback_taux_federal_3)),
            'taux_federal_4': Paie._percent_to_ratio(getattr(fiscal_row, 'taux_federal_4', None) if fiscal_row else None, str(fallback_taux_federal_4)),
            'taux_federal_5': Paie._percent_to_ratio(getattr(fiscal_row, 'taux_federal_5', None) if fiscal_row else None, str(fallback_taux_federal_5)),
            'credit_personnel_quebec_min': getattr(fiscal_row, 'credit_personnel_quebec_min', fallback_credit_personnel_quebec_min) if fiscal_row else fallback_credit_personnel_quebec_min,
            'deduction_travailleur_qc_max_annuelle': getattr(fiscal_row, 'deduction_travailleur_qc_max_annuelle', fallback_deduction_travailleur_qc_max_annuelle) if fiscal_row else fallback_deduction_travailleur_qc_max_annuelle,
            'seuil_qc_1': getattr(fiscal_row, 'seuil_qc_1', fallback_seuil_qc_1) if fiscal_row else fallback_seuil_qc_1,
            'seuil_qc_2': getattr(fiscal_row, 'seuil_qc_2', fallback_seuil_qc_2) if fiscal_row else fallback_seuil_qc_2,
            'seuil_qc_3': getattr(fiscal_row, 'seuil_qc_3', fallback_seuil_qc_3) if fiscal_row else fallback_seuil_qc_3,
            'taux_qc_1': Paie._percent_to_ratio(getattr(fiscal_row, 'taux_qc_1', None) if fiscal_row else None, str(fallback_taux_qc_1)),
            'taux_qc_2': Paie._percent_to_ratio(getattr(fiscal_row, 'taux_qc_2', None) if fiscal_row else None, str(fallback_taux_qc_2)),
            'taux_qc_3': Paie._percent_to_ratio(getattr(fiscal_row, 'taux_qc_3', None) if fiscal_row else None, str(fallback_taux_qc_3)),
            'taux_qc_4': Paie._percent_to_ratio(getattr(fiscal_row, 'taux_qc_4', None) if fiscal_row else None, str(fallback_taux_qc_4)),
            'taux_credit_quebec': Paie._percent_to_ratio(getattr(fiscal_row, 'taux_credit_quebec', None) if fiscal_row else None, str(fallback_taux_credit_quebec)),
        }

    def recalculer(self):
        taux_horaire = self.taux_horaire if self.taux_horaire is not None else self.employe.taux_horaire_defaut
        credit_federal = (
            self.montant_personnel_federal_td1
            if self.montant_personnel_federal_td1 is not None
            else self.employe.montant_personnel_federal_defaut
        )
        credit_quebec = (
            self.montant_personnel_quebec_tp1015
            if self.montant_personnel_quebec_tp1015 is not None
            else self.employe.montant_personnel_quebec_defaut
        )

        self.taux_horaire = taux_horaire
        self.montant_personnel_federal_td1 = credit_federal
        self.montant_personnel_quebec_tp1015 = credit_quebec

        self.vacances_payees = self._decimal_or_zero(self.vacances_payees)
        salaire_brut_periode = (
            self._decimal_or_zero(self.heures_travaillees) * self._decimal_or_zero(taux_horaire)
            + self.vacances_payees
        )
        cumuls = self._cumuls_precedents()
        date_reference = self._date_value(self.periode.date_paie or self.periode.date_fin)
        taux_effectifs = self._taux_effectifs(date_reference)
        resultat = calculer_das(
            DASInputs(
                salaire_brut_periode=salaire_brut_periode,
                periodes_par_annee=self._nombre_periodes_par_annee(),
                montant_personnel_federal_td1=self._decimal_or_zero(credit_federal),
                montant_personnel_quebec_tp1015=self._decimal_or_zero(credit_quebec),
                cumul_salaire_brut_annee=self._decimal_or_zero(cumuls['salaire_brut_periode__sum']),
                cumul_rrq_annee=self._decimal_or_zero(cumuls['rrq__sum']),
                cumul_rqap_annee=self._decimal_or_zero(cumuls['rqap__sum']),
                cumul_ae_annee=self._decimal_or_zero(cumuls['ae__sum']),
                deduction_code_f=self._decimal_or_zero(self.deduction_code_f),
                deduction_tp1015_j=self._decimal_or_zero(self.deduction_tp1015_j),
                deduction_tp1016_j1=self._decimal_or_zero(self.deduction_tp1016_j1),
                retenue_supplementaire_qc=self._decimal_or_zero(self.retenue_supplementaire_qc),
                cotisation_supplementaire_rrq_csa=self._decimal_or_zero(self.cotisation_supplementaire_rrq_csa),
                taux_rrq_employe=self._decimal_or_zero(taux_effectifs['taux_rrq_employe']),
                taux_rrq_supplementaire_2_employe=self._decimal_or_zero(taux_effectifs['taux_rrq_supplementaire_2_employe']),
                exemption_base_rrq=self._decimal_or_zero(taux_effectifs['exemption_base_rrq']),
                max_assurable_rrq=taux_effectifs['max_assurable_rrq'],
                max_supplementaire_rrq=taux_effectifs['max_supplementaire_rrq'],
                taux_rqap_employe=self._decimal_or_zero(taux_effectifs['taux_rqap_employe']),
                max_assurable_rqap=taux_effectifs['max_assurable_rqap'],
                taux_ae_employe=self._decimal_or_zero(taux_effectifs['taux_ae_employe']),
                max_assurable_ae=taux_effectifs['max_assurable_ae'],
                credit_personnel_federal_min=self._decimal_or_zero(taux_effectifs['credit_personnel_federal_min']),
                taux_credit_federal=self._decimal_or_zero(taux_effectifs['taux_credit_federal']),
                montant_canadien_pour_emploi=self._decimal_or_zero(taux_effectifs['montant_canadien_pour_emploi']),
                abattement_federal_quebec=self._decimal_or_zero(taux_effectifs['abattement_federal_quebec']),
                seuil_federal_1=self._decimal_or_zero(taux_effectifs['seuil_federal_1']),
                seuil_federal_2=self._decimal_or_zero(taux_effectifs['seuil_federal_2']),
                seuil_federal_3=self._decimal_or_zero(taux_effectifs['seuil_federal_3']),
                seuil_federal_4=self._decimal_or_zero(taux_effectifs['seuil_federal_4']),
                taux_federal_1=self._decimal_or_zero(taux_effectifs['taux_federal_1']),
                taux_federal_2=self._decimal_or_zero(taux_effectifs['taux_federal_2']),
                taux_federal_3=self._decimal_or_zero(taux_effectifs['taux_federal_3']),
                taux_federal_4=self._decimal_or_zero(taux_effectifs['taux_federal_4']),
                taux_federal_5=self._decimal_or_zero(taux_effectifs['taux_federal_5']),
                credit_personnel_quebec_min=self._decimal_or_zero(taux_effectifs['credit_personnel_quebec_min']),
                deduction_travailleur_qc_max_annuelle=self._decimal_or_zero(taux_effectifs['deduction_travailleur_qc_max_annuelle']),
                seuil_qc_1=self._decimal_or_zero(taux_effectifs['seuil_qc_1']),
                seuil_qc_2=self._decimal_or_zero(taux_effectifs['seuil_qc_2']),
                seuil_qc_3=self._decimal_or_zero(taux_effectifs['seuil_qc_3']),
                taux_qc_1=self._decimal_or_zero(taux_effectifs['taux_qc_1']),
                taux_qc_2=self._decimal_or_zero(taux_effectifs['taux_qc_2']),
                taux_qc_3=self._decimal_or_zero(taux_effectifs['taux_qc_3']),
                taux_qc_4=self._decimal_or_zero(taux_effectifs['taux_qc_4']),
                taux_credit_quebec=self._decimal_or_zero(taux_effectifs['taux_credit_quebec']),
            )
        )

        self.salaire_brut_periode = resultat.salaire_brut_periode
        self.rqap = resultat.rqap
        self.rrq = resultat.rrq
        self.ae = resultat.ae
        self.impot_federal = resultat.impot_federal
        self.impot_provincial = resultat.impot_provincial
        self.total_retenues = resultat.total_retenues
        self.vacances = self._decimal_or_zero(self.vacances)
        self.salaire_net = resultat.salaire_net

    def _needs_recalculation(self):
        if self.pk is None:
            return True

        previous = (
            self.__class__.objects
            .filter(pk=self.pk)
            .values(*self.CALC_INPUT_FIELDS)
            .first()
        )
        if not previous:
            return True

        for field_name in self.CALC_INPUT_FIELDS:
            if previous[field_name] != getattr(self, field_name):
                return True
        return False

    def save(self, *args, **kwargs):
        if self._needs_recalculation():
            self.recalculer()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'Paie de {self.employe} - {self.periode}'