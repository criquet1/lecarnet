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


class Paie(models.Model):
    employe = models.ForeignKey(Employe, on_delete=models.PROTECT, related_name='paies')
    periode = models.ForeignKey(PeriodePaie, on_delete=models.PROTECT, related_name='paies')
    heures_travaillees = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('0.00'))
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

        salaire_brut_periode = self._decimal_or_zero(self.heures_travaillees) * self._decimal_or_zero(taux_horaire)
        cumuls = self._cumuls_precedents()
        resultat = calculer_das(
            DASInputs(
                salaire_brut_periode=salaire_brut_periode,
                periodes_par_annee=self._nombre_periodes_par_annee(),
                montant_personnel_federal_td1=self._decimal_or_zero(credit_federal),
                montant_personnel_quebec_tp1015=self._decimal_or_zero(credit_quebec),
                cumul_rrq_annee=self._decimal_or_zero(cumuls['rrq__sum']),
                cumul_rqap_annee=self._decimal_or_zero(cumuls['rqap__sum']),
                cumul_ae_annee=self._decimal_or_zero(cumuls['ae__sum']),
                deduction_code_f=self._decimal_or_zero(self.deduction_code_f),
                deduction_tp1015_j=self._decimal_or_zero(self.deduction_tp1015_j),
                deduction_tp1016_j1=self._decimal_or_zero(self.deduction_tp1016_j1),
                retenue_supplementaire_qc=self._decimal_or_zero(self.retenue_supplementaire_qc),
                cotisation_supplementaire_rrq_csa=self._decimal_or_zero(self.cotisation_supplementaire_rrq_csa),
            )
        )

        self.salaire_brut_periode = resultat.salaire_brut_periode
        self.rqap = resultat.rqap
        self.rrq = resultat.rrq
        self.ae = resultat.ae
        self.impot_federal = resultat.impot_federal
        self.impot_provincial = resultat.impot_provincial
        self.total_retenues = resultat.total_retenues
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