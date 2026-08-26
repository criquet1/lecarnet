import logging
from datetime import date as date_type, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.db.utils import OperationalError, ProgrammingError
from django.utils.connection import ConnectionDoesNotExist

from paie.services.das import DASInputs, calculer_das
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.cache import cache
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

logger = logging.getLogger(__name__)


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

    # --- nouveaux champs ---
    adresse_postale = models.CharField(max_length=255, blank=True, null=True)
    telephone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(max_length=254, blank=True, null=True)
    nas = models.CharField(max_length=11, blank=True, null=True)  # format: 123-456-789

    class Meta:
        indexes = [
            models.Index(fields=['date_embauche', 'id'], name='paie_employe_date_id_idx'),
        ]
        ordering = ['nom', 'prenom', 'id']

    def _decimal_or_zero(self, valeur):
        if valeur in (None, ''):
            return Decimal('0.00')
        try:
            normalized = str(valeur).strip().replace('\u00a0', '').replace(' ', '').replace('$', '').replace(',', '.')
            return Decimal(normalized)
        except (InvalidOperation, TypeError, ValueError):
            return Decimal('0.00')

    def solde_vacances(self):
        from django.db.models import Sum
        from django.db.models.functions import Coalesce

        result = (
            self.paies
            .aggregate(
                accumule=Coalesce(Sum('vacances'), Decimal('0.00')),
                verse=Coalesce(Sum('vacances_payees'), Decimal('0.00')),
            )
        )
        solde_depart = Decimal('0.00')
        if hasattr(self, 'solde_vacances_depart'):
            solde_depart = self.solde_vacances_depart.montant

        return solde_depart + result['accumule'] - result['verse']

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
            self.date_paie = self._prochaine_date_paie_depuis_settings(kwargs.get('using'))
        super().save(*args, **kwargs)

    def _prochaine_date_paie_depuis_settings(self, using=None):
        from compte.models import Setting
        from paie.services.periodes import (
            DEFAULT_PAYDAY_WEEKDAY,
            next_payday_after,
            payday_weekday_from_anchor,
        )

        db_alias = using or self._state.db or 'default'
        payday_weekday = DEFAULT_PAYDAY_WEEKDAY
        try:
            settings_instance = (
                Setting.objects
                .using(db_alias)
                .only('date_premier_paiement_paie_annee')
                .first()
            )
            payday_weekday = payday_weekday_from_anchor(
                settings_instance.date_premier_paiement_paie_annee if settings_instance else None
            )
        except (ConnectionDoesNotExist, OperationalError, ProgrammingError):
            payday_weekday = DEFAULT_PAYDAY_WEEKDAY

        date_fin = self._meta.get_field('date_fin').to_python(self.date_fin)
        return next_payday_after(date_fin, payday_weekday)

    def __str__(self):
        return f'{self.frequence_paie} - {self.date_fin:%Y-%m-%d}'


class ParametresTauxPaie(models.Model):
    rrq_date_debut_effet = models.DateField()
    rrq_date_fin_effet = models.DateField(blank=True, null=True)

    taux_rrq_employe = models.DecimalField(max_digits=7, decimal_places=5)
    taux_rrq_supplementaire_2_employe = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('4.00000'))
    taux_rrq_premiere_cotisation_supplementaire_employe = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('1.00000'))
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
    constante_qc_1 = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    constante_qc_2 = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    constante_qc_3 = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    constante_qc_4 = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    max_base_credit_rrq_federal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('3768.30'))
    max_ae_credit_federal_qc = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('895.70'))
    taux_rqap_credit_federal = models.DecimalField(max_digits=7, decimal_places=5, default=Decimal('0.43000'))
    max_rqap_credit_federal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('442.90'))
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
    TAUX_HEURES_SUPP = Decimal('1.50')

    employe = models.ForeignKey(Employe, on_delete=models.PROTECT, related_name='paies')
    periode = models.ForeignKey(PeriodePaie, on_delete=models.PROTECT, related_name='paies')
    heures_travaillees = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('0.00'))
    heures_supp = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('0.00'))
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
    gains_assurables_ae = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))       # case 24 T4
    gains_ouvrant_droit_rrq = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))   # case 26 T4 / case B RL1
    rrq_employeur = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    rqap_employeur = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    ae_employeur = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    cnesst_employeur = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    fss_employeur = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

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
        'heures_supp',
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

    CACHE_KEY_TAUX_ROWS = 'paie_parametrestauxpaie_rows'
    CACHE_TTL_TAUX_ROWS = 3600  # 1 heure, filet de sécurité si un signal était manqué

    @staticmethod
    def _get_taux_rows():
        rows = cache.get(Paie.CACHE_KEY_TAUX_ROWS)
        if rows is None:
            rows = list(ParametresTauxPaie.objects.using('default').all())
            cache.set(Paie.CACHE_KEY_TAUX_ROWS, rows, Paie.CACHE_TTL_TAUX_ROWS)
        return rows

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
    def _percent_to_ratio(value):
        return Decimal(str(value)) / Decimal('100')

    @staticmethod
    def _rate_to_ratio(value):
        raw = Decimal(str(value))
        if raw <= Decimal('1'):
            return raw
        return raw / Decimal('100')

    @staticmethod
    def _taux_effectifs(date_reference):
        rows = Paie._get_taux_rows()

        fallback = {
            'taux_rrq_employe': Decimal('6.30000'),
            'taux_rrq_supplementaire_2_employe': Decimal('4.00000'),
            'taux_rrq_premiere_cotisation_supplementaire_employe': Decimal('1.00000'),
            'exemption_base_rrq': Decimal('3500.00'),
            'max_assurable_rrq': Decimal('74600.00'),
            'max_supplementaire_rrq': Decimal('85000.00'),
            'taux_rqap_employe': Decimal('0.43000'),
            'max_assurable_rqap': Decimal('98700.00'),
            'taux_ae_employe': Decimal('1.30000'),
            'max_assurable_ae': Decimal('67500.00'),
            'credit_personnel_federal_min': Decimal('16452.00'),
            'taux_credit_federal': Decimal('14.00000'),
            'montant_canadien_pour_emploi': Decimal('1501.00'),
            'abattement_federal_quebec': Decimal('16.50000'),
            'seuil_federal_1': Decimal('58523.00'),
            'seuil_federal_2': Decimal('117045.00'),
            'seuil_federal_3': Decimal('181440.00'),
            'seuil_federal_4': Decimal('258482.00'),
            'taux_federal_1': Decimal('14.00000'),
            'taux_federal_2': Decimal('20.50000'),
            'taux_federal_3': Decimal('26.00000'),
            'taux_federal_4': Decimal('29.00000'),
            'taux_federal_5': Decimal('33.00000'),
            'credit_personnel_quebec_min': Decimal('18952.00'),
            'deduction_travailleur_qc_max_annuelle': Decimal('1450.00'),
            'seuil_qc_1': Decimal('54345.00'),
            'seuil_qc_2': Decimal('108680.00'),
            'seuil_qc_3': Decimal('132245.00'),
            'taux_qc_1': Decimal('14.00000'),
            'taux_qc_2': Decimal('19.00000'),
            'taux_qc_3': Decimal('24.00000'),
            'taux_qc_4': Decimal('25.75000'),
            'constante_qc_1': Decimal('0.00'),
            'constante_qc_2': Decimal('2717.00'),
            'constante_qc_3': Decimal('8151.00'),
            'constante_qc_4': Decimal('10465.00'),
            'max_base_credit_rrq_federal': Decimal('3768.30'),
            'max_ae_credit_federal_qc': Decimal('895.70'),
            'taux_rqap_credit_federal': Decimal('0.43000'),
            'max_rqap_credit_federal': Decimal('442.90'),
            'taux_credit_quebec': Decimal('14.00000'),
        }

        def _pick(start_field, end_field):
            active_candidates = [
                row for row in rows
                if getattr(row, start_field) <= date_reference and (getattr(row, end_field) is None or getattr(row, end_field) >= date_reference)
            ]
            if active_candidates:
                return sorted(active_candidates, key=lambda row: (getattr(row, start_field), row.id), reverse=True)[0]

            started_candidates = [
                row for row in rows
                if getattr(row, start_field) <= date_reference
            ]
            if started_candidates:
                return sorted(started_candidates, key=lambda row: (getattr(row, start_field), row.id), reverse=True)[0]

            future_candidates = [
                row for row in rows
                if getattr(row, start_field) > date_reference
            ]
            if future_candidates:
                return sorted(future_candidates, key=lambda row: (getattr(row, start_field), row.id))[0]
            return None

        rrq_row = _pick('rrq_date_debut_effet', 'rrq_date_fin_effet')
        rqap_row = _pick('rqap_date_debut_effet', 'rqap_date_fin_effet')
        ae_row = _pick('ae_date_debut_effet', 'ae_date_fin_effet')

        fiscal_row = rrq_row or rqap_row or ae_row

        def _value(row, field_name):
            value = getattr(row, field_name, None) if row else None
            return fallback[field_name] if value is None else value

        rrq_exemption = Decimal(str(_value(rrq_row, 'exemption_base_rrq')))
        rrq_max_assurable = Decimal(str(_value(rrq_row, 'max_assurable_rrq')))
        rrq_max_supplementaire = Decimal(str(_value(rrq_row, 'max_supplementaire_rrq')))

        if rrq_exemption < 0 or rrq_exemption > Decimal('10000'):
            rrq_exemption = fallback['exemption_base_rrq']
        if rrq_max_assurable < Decimal('10000'):
            rrq_max_assurable = fallback['max_assurable_rrq']
        if rrq_max_supplementaire < Decimal('10000'):
            rrq_max_supplementaire = fallback['max_supplementaire_rrq']
        if rrq_max_supplementaire < rrq_max_assurable:
            rrq_max_supplementaire = rrq_max_assurable

        rrq_rate = Paie._rate_to_ratio(_value(rrq_row, 'taux_rrq_employe'))
        rrq_supp2_rate = Paie._rate_to_ratio(_value(rrq_row, 'taux_rrq_supplementaire_2_employe'))
        rrq_premiere_supp_rate = Paie._rate_to_ratio(_value(rrq_row, 'taux_rrq_premiere_cotisation_supplementaire_employe'))
        if rrq_rate <= 0 or rrq_rate <= rrq_supp2_rate:
            rrq_rate = Paie._rate_to_ratio(fallback['taux_rrq_employe'])
        if rrq_supp2_rate < 0:
            rrq_supp2_rate = Paie._rate_to_ratio(fallback['taux_rrq_supplementaire_2_employe'])
        if rrq_premiere_supp_rate < 0 or rrq_premiere_supp_rate >= rrq_rate:
            rrq_premiere_supp_rate = Paie._rate_to_ratio(fallback['taux_rrq_premiere_cotisation_supplementaire_employe'])
        if rqap_row is None or rqap_row.taux_rqap_employeur is None:
            raise ValidationError(
                "Aucun taux RQAP employeur configure pour la date %s. "
                "Ajoutez une ligne ParametresTauxPaie couvrant cette periode." % date_reference
            )
        if ae_row is None or ae_row.taux_ae_employeur is None:
            raise ValidationError(
                "Aucun taux AE employeur configure pour la date %s. "
                "Ajoutez une ligne ParametresTauxPaie couvrant cette periode." % date_reference
            )


        return {
            'taux_rrq_employe': rrq_rate,
            'taux_rrq_supplementaire_2_employe': rrq_supp2_rate,
            'taux_rrq_premiere_cotisation_supplementaire_employe': rrq_premiere_supp_rate,
            'exemption_base_rrq': rrq_exemption,
            'max_assurable_rrq': rrq_max_assurable,
            'max_supplementaire_rrq': rrq_max_supplementaire,
            'taux_rqap_employe': Paie._percent_to_ratio(_value(rqap_row, 'taux_rqap_employe')),
            'taux_rqap_employeur': Paie._percent_to_ratio(rqap_row.taux_rqap_employeur),
            'max_assurable_rqap': _value(rqap_row, 'max_assurable_rqap'),
            'taux_ae_employe': Paie._percent_to_ratio(_value(ae_row, 'taux_ae_employe')),
            'taux_ae_employeur': Paie._percent_to_ratio(ae_row.taux_ae_employeur),
            'max_assurable_ae': _value(ae_row, 'max_assurable_ae'),
            'credit_personnel_federal_min': _value(fiscal_row, 'credit_personnel_federal_min'),
            'taux_credit_federal': Paie._percent_to_ratio(_value(fiscal_row, 'taux_credit_federal')),
            'montant_canadien_pour_emploi': _value(fiscal_row, 'montant_canadien_pour_emploi'),
            'abattement_federal_quebec': Paie._percent_to_ratio(_value(fiscal_row, 'abattement_federal_quebec')),
            'seuil_federal_1': _value(fiscal_row, 'seuil_federal_1'),
            'seuil_federal_2': _value(fiscal_row, 'seuil_federal_2'),
            'seuil_federal_3': _value(fiscal_row, 'seuil_federal_3'),
            'seuil_federal_4': _value(fiscal_row, 'seuil_federal_4'),
            'taux_federal_1': Paie._percent_to_ratio(_value(fiscal_row, 'taux_federal_1')),
            'taux_federal_2': Paie._percent_to_ratio(_value(fiscal_row, 'taux_federal_2')),
            'taux_federal_3': Paie._percent_to_ratio(_value(fiscal_row, 'taux_federal_3')),
            'taux_federal_4': Paie._percent_to_ratio(_value(fiscal_row, 'taux_federal_4')),
            'taux_federal_5': Paie._percent_to_ratio(_value(fiscal_row, 'taux_federal_5')),
            'credit_personnel_quebec_min': _value(fiscal_row, 'credit_personnel_quebec_min'),
            'deduction_travailleur_qc_max_annuelle': _value(fiscal_row, 'deduction_travailleur_qc_max_annuelle'),
            'seuil_qc_1': _value(fiscal_row, 'seuil_qc_1'),
            'seuil_qc_2': _value(fiscal_row, 'seuil_qc_2'),
            'seuil_qc_3': _value(fiscal_row, 'seuil_qc_3'),
            'taux_qc_1': Paie._percent_to_ratio(_value(fiscal_row, 'taux_qc_1')),
            'taux_qc_2': Paie._percent_to_ratio(_value(fiscal_row, 'taux_qc_2')),
            'taux_qc_3': Paie._percent_to_ratio(_value(fiscal_row, 'taux_qc_3')),
            'taux_qc_4': Paie._percent_to_ratio(_value(fiscal_row, 'taux_qc_4')),
            'constante_qc_1': _value(fiscal_row, 'constante_qc_1'),
            'constante_qc_2': _value(fiscal_row, 'constante_qc_2'),
            'constante_qc_3': _value(fiscal_row, 'constante_qc_3'),
            'constante_qc_4': _value(fiscal_row, 'constante_qc_4'),
            'max_base_credit_rrq_federal': _value(fiscal_row, 'max_base_credit_rrq_federal'),
            'max_ae_credit_federal_qc': _value(fiscal_row, 'max_ae_credit_federal_qc'),
            'taux_rqap_credit_federal': Paie._percent_to_ratio(_value(fiscal_row, 'taux_rqap_credit_federal')),
            'max_rqap_credit_federal': _value(fiscal_row, 'max_rqap_credit_federal'),
            'taux_credit_quebec': Paie._percent_to_ratio(_value(fiscal_row, 'taux_credit_quebec')),
        }

    def _taux_employeur_tenant(self):
        from compte.models import Setting

        db_alias = self._state.db or 'default'
        try:
            settings_instance = (
                Setting.objects.using(db_alias)
                .only('taux_cnesst_employeur', 'taux_fss_employeur')
                .first()
            )
        except (ConnectionDoesNotExist, OperationalError, ProgrammingError):
            settings_instance = None

        if settings_instance is None:
            raise ValidationError(
                "Aucune configuration Setting trouvee pour ce tenant (base %s). "
                "Impossible de calculer les cotisations CNESST/FSS employeur." % db_alias
            )
        if settings_instance.taux_cnesst_employeur is None:
            raise ValidationError(
                "Le taux CNESST employeur n'est pas configure dans Parametres (Setting) pour ce tenant."
            )
        if settings_instance.taux_fss_employeur is None:
            raise ValidationError(
                "Le taux FSS employeur n'est pas configure dans Parametres (Setting) pour ce tenant."
            )

        taux_cnesst = self._decimal_or_zero(settings_instance.taux_cnesst_employeur)
        taux_fss = self._decimal_or_zero(settings_instance.taux_fss_employeur)
        return taux_cnesst, taux_fss
    
    def _gains_plafonnes(self, salaire_brut_periode, cumul_brut_precedent, plafond):
        plafond = self._decimal_or_zero(plafond)
        cumul_brut_precedent = self._decimal_or_zero(cumul_brut_precedent)
        espace_restant = plafond - min(cumul_brut_precedent, plafond)
        if espace_restant <= 0:
            return Decimal('0.00')
        return min(salaire_brut_periode, espace_restant)


    def recalculer(self):
        taux_horaire = self.taux_horaire if self.taux_horaire is not None else self.employe.taux_horaire_defaut
        heures_travaillees = self._decimal_or_zero(self.heures_travaillees)
        heures_supp = self._decimal_or_zero(self.heures_supp)
        taux_horaire = self._decimal_or_zero(taux_horaire)
        if (heures_travaillees > 0 or heures_supp > 0) and taux_horaire <= 0:
            raise ValidationError(
                'Le taux horaire de cet employe est absent ou invalide. Corrigez sa fiche avant de calculer la paie.'
            )

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
            heures_travaillees * taux_horaire
            + heures_supp * taux_horaire * self.TAUX_HEURES_SUPP
            + self.vacances_payees
        )
        cumuls = self._cumuls_precedents()
        date_reference = self._date_value(self.periode.date_paie or self.periode.date_fin)
        taux_effectifs = self._taux_effectifs(date_reference)

        cumul_brut_precedent = self._decimal_or_zero(cumuls['salaire_brut_periode__sum'])

        self.gains_assurables_ae = self._gains_plafonnes(
            salaire_brut_periode, cumul_brut_precedent, taux_effectifs['max_assurable_ae']
        )
        self.gains_ouvrant_droit_rrq = self._gains_plafonnes(
            salaire_brut_periode, cumul_brut_precedent, taux_effectifs['max_supplementaire_rrq']
        )

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
                taux_rrq_premiere_cotisation_supplementaire_employe=self._decimal_or_zero(taux_effectifs['taux_rrq_premiere_cotisation_supplementaire_employe']),
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
                constante_qc_1=self._decimal_or_zero(taux_effectifs['constante_qc_1']),
                constante_qc_2=self._decimal_or_zero(taux_effectifs['constante_qc_2']),
                constante_qc_3=self._decimal_or_zero(taux_effectifs['constante_qc_3']),
                constante_qc_4=self._decimal_or_zero(taux_effectifs['constante_qc_4']),
                taux_credit_quebec=self._decimal_or_zero(taux_effectifs['taux_credit_quebec']),
                max_base_credit_rrq_federal=self._decimal_or_zero(taux_effectifs['max_base_credit_rrq_federal']),
                max_ae_credit_federal_qc=self._decimal_or_zero(taux_effectifs['max_ae_credit_federal_qc']),
                taux_rqap_credit_federal=self._decimal_or_zero(taux_effectifs['taux_rqap_credit_federal']),
                max_rqap_credit_federal=self._decimal_or_zero(taux_effectifs['max_rqap_credit_federal']),
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

        # --- Cotisations employeur ---
        self.rrq_employeur = self.rrq  # dollar pour dollar, confirmé

        taux_rqap_employe = taux_effectifs['taux_rqap_employe']
        if taux_rqap_employe <= 0:
            raise ValidationError(
                "Le taux RQAP employe est nul ou invalide, impossible de deriver la part employeur."
            )
        self.rqap_employeur = (
            self.rqap * taux_effectifs['taux_rqap_employeur'] / taux_rqap_employe
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        self.ae_employeur = (
            self.gains_assurables_ae * taux_effectifs['taux_ae_employeur']
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        taux_cnesst, taux_fss = self._taux_employeur_tenant()
        self.cnesst_employeur = (self.salaire_brut_periode * taux_cnesst).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        self.fss_employeur = (self.salaire_brut_periode * taux_fss).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)



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


class SoldeVacancesDepart(models.Model):
    employe = models.OneToOneField(
        Employe,
        on_delete=models.CASCADE,
        related_name='solde_vacances_depart',
    )
    montant = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        verbose_name = "Solde de départ vacances"
        verbose_name_plural = "Soldes de départ vacances"

    def __str__(self):
        return f"{self.employe} - {self.montant}"


@receiver(post_save, sender=ParametresTauxPaie)
@receiver(post_delete, sender=ParametresTauxPaie)
def invalider_cache_taux_paie(sender, **kwargs):
    cache.delete(Paie.CACHE_KEY_TAUX_ROWS)


@receiver(post_save, sender=ParametresTauxPaie)
def prefill_periodes_sur_nouveaux_taux(sender, instance, created, **kwargs):
    """
    Quand un nouveau bloc de taux est créé, pré-remplit les PeriodePaie
    pour toutes les années couvertes par ce bloc dans chaque tenant actif.
    """
    if not created:
        return

    from tenancy.models import ClientDatabase
    from paie.services.periodes import generate_periodes_annee

    start_year = instance.rrq_date_debut_effet.year
    end_year = instance.rrq_date_fin_effet.year if instance.rrq_date_fin_effet else start_year

    tenant_aliases = list(
        ClientDatabase.objects.filter(is_active=True).values_list('db_alias', flat=True)
    )

    for annee in range(start_year, end_year + 1):
        for db_alias in tenant_aliases:
            try:
                generate_periodes_annee(annee, db_alias)
            except (ConnectionDoesNotExist, OperationalError, ProgrammingError) as exc:
                logger.warning(
                    "Pré-remplissage des périodes impossible pour tenant=%s annee=%s: %s",
                    db_alias,
                    annee,
                    exc,
                )


class FeuilletFiscalAnnuel(models.Model):
    employe = models.ForeignKey(Employe, on_delete=models.PROTECT, related_name="feuillets_fiscaux")
    annee = models.PositiveIntegerField()

    # T4
    t4_revenu_emploi = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    t4_cotisation_rrq = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    t4_cotisation_ae = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    t4_impot_federal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    t4_gains_assurables_ae = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    t4_gains_ouvrant_droit_rrq = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    # Relevé 1
    rl1_revenu_emploi = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    rl1_cotisation_rrq = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    rl1_cotisation_rqap = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    rl1_impot_quebec = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    date_generation = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["employe", "annee"], name="feuillet_unique_employe_annee"),
        ]

    @classmethod
    def generer_pour_annee(cls, employe, annee):          # <-- le bloc "totaux" va ICI, indenté sous cette méthode
        totaux = Paie.objects.filter(
            employe=employe,
            periode__date_paie__year=annee,
        ).aggregate(
            salaire_brut=Sum("salaire_brut_periode"),
            rrq=Sum("rrq"),
            rqap=Sum("rqap"),
            ae=Sum("ae"),
            impot_federal=Sum("impot_federal"),
            impot_provincial=Sum("impot_provincial"),
            gains_assurables_ae=Sum("gains_assurables_ae"),
            gains_ouvrant_droit_rrq=Sum("gains_ouvrant_droit_rrq"),
        )

        feuillet, _ = cls.objects.update_or_create(
            employe=employe,
            annee=annee,
            defaults={
                "t4_revenu_emploi": totaux["salaire_brut"] or Decimal("0.00"),
                "t4_cotisation_rrq": totaux["rrq"] or Decimal("0.00"),
                "t4_cotisation_ae": totaux["ae"] or Decimal("0.00"),
                "t4_impot_federal": totaux["impot_federal"] or Decimal("0.00"),
                "t4_gains_assurables_ae": totaux["gains_assurables_ae"] or Decimal("0.00"),
                "t4_gains_ouvrant_droit_rrq": totaux["gains_ouvrant_droit_rrq"] or Decimal("0.00"),
                "rl1_revenu_emploi": totaux["salaire_brut"] or Decimal("0.00"),
                "rl1_cotisation_rrq": totaux["rrq"] or Decimal("0.00"),
                "rl1_cotisation_rqap": totaux["rqap"] or Decimal("0.00"),
                "rl1_impot_quebec": totaux["impot_provincial"] or Decimal("0.00"),
            },
        )
        return feuillet

    def __str__(self):
        return f"Feuillet {self.annee} — {self.employe}"


class PaieJournalLigne(models.Model):
    paie_id = models.IntegerField(primary_key=True)
    employe_nom = models.CharField(max_length=100)
    employe_prenom = models.CharField(max_length=100)
    date_fin = models.DateField()
    date_paie = models.DateField(null=True, blank=True)
    brut = models.DecimalField(max_digits=10, decimal_places=2)
    net = models.DecimalField(max_digits=10, decimal_places=2)
    rrq_employe = models.DecimalField(max_digits=10, decimal_places=2)
    rrq_employeur = models.DecimalField(max_digits=10, decimal_places=2)
    rqap_employe = models.DecimalField(max_digits=10, decimal_places=2)
    rqap_employeur = models.DecimalField(max_digits=10, decimal_places=2)
    ae_employe = models.DecimalField(max_digits=10, decimal_places=2)
    ae_employeur = models.DecimalField(max_digits=10, decimal_places=2)
    cnesst_employeur = models.DecimalField(max_digits=10, decimal_places=2)
    fss_employeur = models.DecimalField(max_digits=10, decimal_places=2)
    impot_federal = models.DecimalField(max_digits=10, decimal_places=2)
    impot_provincial = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        managed = False
        db_table = 'paie_journal_lignes'