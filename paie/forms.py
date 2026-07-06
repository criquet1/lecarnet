from calendar import monthrange
from datetime import date as date_type
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from holidays import country_holidays

from facture.utils import get_setting

from .models import Employe, FrequencePaie, Paie, PeriodePaie


class EmployeForm(forms.ModelForm):
    class Meta:
        model = Employe
        fields = [
            'nom',
            'prenom',
            'date_embauche',
            'salH',
            'e_prov',
            'e_fed',
            'frequence_paie',
            'actif',
        ]
        widgets = {
            'nom': forms.TextInput(attrs={'class': 'form-control'}),
            'prenom': forms.TextInput(attrs={'class': 'form-control'}),
            'date_embauche': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'salH': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex.: 25.00'}),
            'e_prov': forms.NumberInput(attrs={'class': 'form-control', 'step': '1'}),
            'e_fed': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex.: 16452'}),
            'frequence_paie': forms.Select(attrs={'class': 'form-select'}),
            'actif': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'salH': 'Taux horaire',
            'e_prov': 'Credit personnel Quebec',
            'e_fed': 'Credit personnel federal',
            'frequence_paie': 'Frequence de paie',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['frequence_paie'].queryset = FrequencePaie.objects.order_by('nombre_periodes_par_annee', 'code')
        self.fields['frequence_paie'].required = False

        if not self.instance.pk:
            settings_instance = get_setting('frequence_paie')
            if settings_instance and settings_instance.frequence_paie_id:
                self.fields['frequence_paie'].initial = settings_instance.frequence_paie_id


class PaieForm(forms.ModelForm):
    CODE_F_COMPONENT_FIELDS = [
        'code_f_rpa',
        'code_f_reer',
        'code_f_rver_rpac',
        'code_f_celiapp',
        'code_f_convention_retraite',
        'code_f_ric',
        'code_f_voyages_region_eloignee',
        'code_f_option_achat_titres',
        'code_f_partie_remuneration_admissible',
    ]
    CODE_F_PARTIE_COMPONENT_FIELDS = [
        'code_f_partie_reserve',
        'code_f_partie_specialiste_etranger',
        'code_f_partie_chercheur_etranger',
        'code_f_partie_chercheur_postdoc',
        'code_f_partie_expert_etranger',
        'code_f_partie_professeur_etranger',
        'code_f_partie_producteur_poste_cle',
        'code_f_partie_travailleur_agricole_etranger',
        'code_f_partie_forces_canadiennes_police',
    ]

    periode_date_fin = forms.ChoiceField(
        required=False,
        choices=[('', '---')],
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Fin de période',
    )
    periode_date_paie = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'readonly': 'readonly'}),
        label='Date de paiement',
    )
    code_f_rpa = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Cotisation a un RPA')
    code_f_reer = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Cotisation a un REER')
    code_f_rver_rpac = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Cotisation a un RVER ou a un RPAC')
    code_f_celiapp = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Cotisation a un CELIAPP')
    code_f_convention_retraite = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Cotisation a une convention de retraite')
    code_f_ric = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Deduction relative au RIC')
    code_f_voyages_region_eloignee = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Deduction voyages region eloignee')
    code_f_option_achat_titres = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Deduction option d achat de titres')
    code_f_partie_remuneration_admissible = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control bg-light', 'step': '0.01', 'readonly': 'readonly'}), label='Partie de remuneration admissible')
    code_f_partie_reserve = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Deduction pour revenus situes dans une reserve')
    code_f_partie_specialiste_etranger = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Deduction pour specialiste etranger')
    code_f_partie_chercheur_etranger = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Deduction pour chercheur etranger')
    code_f_partie_chercheur_postdoc = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Deduction pour chercheur etranger en stage postdoctoral')
    code_f_partie_expert_etranger = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Deduction pour expert etranger')
    code_f_partie_professeur_etranger = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Deduction pour professeur etranger')
    code_f_partie_producteur_poste_cle = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Deduction pour producteur etranger et particulier etranger en poste cle au Quebec')
    code_f_partie_travailleur_agricole_etranger = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Deduction pour travailleur agricole etranger')
    code_f_partie_forces_canadiennes_police = forms.DecimalField(required=False, max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='Deduction pour le personnel des Forces canadiennes et des forces policieres')

    class Meta:
        model = Paie
        fields = [
            'employe',
            'heures_travaillees',
            'montant_personnel_federal_td1',
            'montant_personnel_quebec_tp1015',
            'deduction_code_f',
            'deduction_tp1015_j',
            'deduction_tp1016_j1',
            'retenue_supplementaire_qc',
            'cotisation_supplementaire_rrq_csa',
        ]
        widgets = {
            'employe': forms.Select(attrs={'class': 'form-select'}),
            'heures_travaillees': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'montant_personnel_federal_td1': forms.HiddenInput(),
            'montant_personnel_quebec_tp1015': forms.HiddenInput(),
            'deduction_code_f': forms.HiddenInput(),
            'deduction_tp1015_j': forms.HiddenInput(),
            'deduction_tp1016_j1': forms.HiddenInput(),
            'retenue_supplementaire_qc': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'cotisation_supplementaire_rrq_csa': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }
        labels = {
            'deduction_code_f': 'Total des sommes suivantes pour la periode de paie',
            'deduction_tp1015_j': 'Deduction TP-1015 J',
            'deduction_tp1016_j1': 'Deduction TP-1016 J1',
            'retenue_supplementaire_qc': 'Retenue supplementaire Quebec',
            'cotisation_supplementaire_rrq_csa': 'Cotisation supplementaire RRQ (CSA)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._selected_candidate = None
        self.fields['employe'].queryset = Employe.objects.filter(actif=True).select_related('frequence_paie').order_by('nom', 'prenom', 'id')

        if self.is_bound:
            employe_id = self.data.get('employe')
            if employe_id:
                try:
                    employe = Employe.objects.select_related('frequence_paie').get(pk=employe_id, actif=True)
                    options_payload, default_value, _ = self.options_fin_periode_annee_courante(employe)
                    self.fields['periode_date_fin'].choices = [('', '---')] + [
                        (o['value'], o['label']) for o in options_payload
                    ]
                    if not self.data.get('periode_date_fin') and default_value:
                        self.initial['periode_date_fin'] = default_value
                except Employe.DoesNotExist:
                    pass
        optional_decimal_fields = [
            'montant_personnel_federal_td1',
            'montant_personnel_quebec_tp1015',
            'deduction_code_f',
            'deduction_tp1015_j',
            'deduction_tp1016_j1',
            'retenue_supplementaire_qc',
            'cotisation_supplementaire_rrq_csa',
        ]
        for field_name in optional_decimal_fields:
            self.fields[field_name].required = False

    @staticmethod
    def _add_months(base_date, months):
        month_index = (base_date.month - 1) + months
        year = base_date.year + (month_index // 12)
        month = (month_index % 12) + 1
        day = min(base_date.day, monthrange(year, month)[1])
        return date_type(year, month, day)

    @classmethod
    def _build_projected_periods(cls, frequence, date_debut_anchor, date_paiement_anchor, count=160):
        periods = []
        code = frequence.code
        delai_paiement = timedelta(0)

        # Calcul du delai paiement base sur la premiere periode.
        if code == FrequencePaie.HEBDOMADAIRE:
            first_end = date_debut_anchor + timedelta(days=6)
        elif code == FrequencePaie.AUX_2_SEMAINES:
            first_end = date_debut_anchor + timedelta(days=13)
        elif code == FrequencePaie.PAR_MOIS:
            next_start = cls._add_months(date_debut_anchor, 1)
            first_end = next_start - timedelta(days=1)
        elif code == FrequencePaie.DEUX_FOIS_MOIS:
            if date_debut_anchor.day <= 15:
                first_end = date_debut_anchor.replace(day=15)
            else:
                last_day = monthrange(date_debut_anchor.year, date_debut_anchor.month)[1]
                first_end = date_debut_anchor.replace(day=last_day)
        else:
            return periods

        if date_paiement_anchor:
            delai_paiement = date_paiement_anchor - first_end

        if code in (FrequencePaie.HEBDOMADAIRE, FrequencePaie.AUX_2_SEMAINES):
            step_days = 7 if code == FrequencePaie.HEBDOMADAIRE else 14
            for idx in range(count):
                date_debut = date_debut_anchor + timedelta(days=idx * step_days)
                date_fin = date_debut + timedelta(days=step_days - 1)
                date_paie = cls._to_business_day(date_fin + delai_paiement)
                periods.append((date_debut, date_fin, date_paie))
            return periods

        if code == FrequencePaie.PAR_MOIS:
            current_start = date_debut_anchor
            for _ in range(count):
                next_start = cls._add_months(current_start, 1)
                date_fin = next_start - timedelta(days=1)
                date_paie = cls._to_business_day(date_fin + delai_paiement)
                periods.append((current_start, date_fin, date_paie))
                current_start = next_start
            return periods

        if code == FrequencePaie.DEUX_FOIS_MOIS:
            current_start = date_debut_anchor
            for _ in range(count):
                if current_start.day <= 15:
                    date_fin = current_start.replace(day=15)
                else:
                    last_day = monthrange(current_start.year, current_start.month)[1]
                    date_fin = current_start.replace(day=last_day)
                date_paie = cls._to_business_day(date_fin + delai_paiement)
                periods.append((current_start, date_fin, date_paie))
                current_start = date_fin + timedelta(days=1)
            return periods

        return periods

    def _code_f_total(self, cleaned_data):
        total = Decimal('0.00')
        for field_name in self.CODE_F_COMPONENT_FIELDS:
            total += cleaned_data.get(field_name) or Decimal('0.00')
        return total

    @staticmethod
    def _is_weekend(value):
        return bool(value and value.weekday() >= 5)

    @classmethod
    def _is_holiday(cls, value):
        if not value:
            return False
        ca_qc_holidays = country_holidays('CA', subdiv='QC', years=[value.year])
        return value in ca_qc_holidays

    @classmethod
    def _to_business_day(cls, value):
        if not value:
            return value
        adjusted = value
        while cls._is_weekend(adjusted) or cls._is_holiday(adjusted):
            adjusted = adjusted - timedelta(days=1)
        return adjusted

    def _code_f_partie_total(self, cleaned_data):
        total = Decimal('0.00')
        for field_name in self.CODE_F_PARTIE_COMPONENT_FIELDS:
            total += cleaned_data.get(field_name) or Decimal('0.00')
        return total

    @staticmethod
    def _frequence_employe_ou_setting(employe):
        if employe and employe.frequence_paie_id:
            return employe.frequence_paie

        settings_instance = get_setting('frequence_paie')
        if settings_instance and settings_instance.frequence_paie_id:
            return settings_instance.frequence_paie
        return None

    @staticmethod
    def _paie_settings():
        return get_setting(
            'frequence_paie',
            'date_debut_periode_paie_annee',
            'date_premier_paiement_paie_annee',
        )

    @classmethod
    def _find_candidate_from_existing(cls, employe, frequence):
        periode_existante = (
            PeriodePaie.objects
            .filter(frequence_paie=frequence, fermee=False)
            .exclude(paies__employe=employe)
            .order_by('date_fin', 'id')
            .first()
        )
        if not periode_existante:
            return None
        return {
            'mode': 'existing',
            'periode': periode_existante,
            'date_debut': periode_existante.date_debut,
            'date_fin': periode_existante.date_fin,
            'date_paie': periode_existante.date_paie or periode_existante.date_fin,
            'frequence': frequence,
        }

    @classmethod
    def _build_candidates_with_projection(cls, employe, frequence):
        settings_instance = cls._paie_settings()
        date_debut_anchor = settings_instance.date_debut_periode_paie_annee if settings_instance else None
        date_paie_anchor = settings_instance.date_premier_paiement_paie_annee if settings_instance else None

        if not date_debut_anchor:
            derniere_periode = (
                PeriodePaie.objects
                .filter(frequence_paie=frequence)
                .order_by('-date_fin', '-id')
                .first()
            )
            if not derniere_periode or not derniere_periode.date_fin:
                return []
            date_debut_anchor = derniere_periode.date_debut or (derniere_periode.date_fin + timedelta(days=1))
            date_paie_anchor = derniere_periode.date_paie or derniere_periode.date_fin

        projected = cls._build_projected_periods(frequence, date_debut_anchor, date_paie_anchor)
        existing_map = {
            (p.date_debut, p.date_fin): p
            for p in PeriodePaie.objects.filter(frequence_paie=frequence)
        }

        candidates = []
        for date_debut, date_fin, date_paie in projected:
            existing = existing_map.get((date_debut, date_fin))
            if existing and existing.fermee:
                continue
            if existing and Paie.objects.filter(employe=employe, periode=existing).exists():
                continue

            if existing:
                candidates.append({
                    'mode': 'existing',
                    'periode': existing,
                    'date_debut': existing.date_debut,
                    'date_fin': existing.date_fin,
                    'date_paie': existing.date_paie or existing.date_fin,
                    'frequence': frequence,
                })
            else:
                candidates.append({
                    'mode': 'projected',
                    'periode': None,
                    'date_debut': date_debut,
                    'date_fin': date_fin,
                    'date_paie': date_paie,
                    'frequence': frequence,
                })

        return candidates

    @classmethod
    def suggestions_periode_pour_employe(cls, employe):
        frequence = cls._frequence_employe_ou_setting(employe)
        if not frequence:
            return None, None, 'Aucune frequence de paie configuree pour cet employe ni dans les parametres.'

        candidate_existing = cls._find_candidate_from_existing(employe, frequence)
        candidates = cls._build_candidates_with_projection(employe, frequence)
        if candidate_existing and not any(
            c['date_debut'] == candidate_existing['date_debut'] and c['date_fin'] == candidate_existing['date_fin'] for c in candidates
        ):
            candidates.insert(0, candidate_existing)

        if not candidates:
            return None, None, 'Aucune periode de paie disponible pour cet employe.'

        last_pay_date = (
            Paie.objects
            .filter(employe=employe)
            .order_by('-periode__date_paie', '-periode__date_fin', '-id')
            .values_list('periode__date_paie', flat=True)
            .first()
        )
        today = date_type.today()

        if last_pay_date:
            next_candidate = min(
                (c for c in candidates if c['date_paie'] and c['date_paie'] > last_pay_date),
                key=lambda c: c['date_paie'],
                default=None,
            )
        else:
            next_candidate = min(
                (c for c in candidates if c['date_paie']),
                key=lambda c: c['date_paie'],
                default=None,
            )

        near_today_candidate = min(
            (c for c in candidates if c['date_paie']),
            key=lambda c: (abs((c['date_paie'] - today).days), c['date_paie'] < today, c['date_paie']),
            default=None,
        )

        if not next_candidate:
            next_candidate = near_today_candidate
        if not near_today_candidate:
            near_today_candidate = next_candidate

        return next_candidate, near_today_candidate, None

    @classmethod
    def options_fin_periode_annee_courante(cls, employe):
        frequence = cls._frequence_employe_ou_setting(employe)
        if not frequence:
            return [], None, 'Aucune frequence de paie configuree pour cet employe ni dans les parametres.'

        candidates = cls._build_candidates_with_projection(employe, frequence)
        annee = date_type.today().year
        year_candidates = [c for c in candidates if c['date_fin'] and c['date_fin'].year == annee]
        if not year_candidates:
            return [], None, 'Aucune fin de periode disponible pour l annee en cours.'

        next_candidate, near_today_candidate, _ = cls.suggestions_periode_pour_employe(employe)

        def _value(candidate):
            return candidate['date_fin'].isoformat() if candidate and candidate.get('date_fin') else None

        next_value = _value(next_candidate)
        near_value = _value(near_today_candidate)
        valid_values = {c['date_fin'].isoformat() for c in year_candidates}

        default_value = None
        if next_value in valid_values:
            default_value = next_value
        elif near_value in valid_values:
            default_value = near_value
        else:
            default_value = year_candidates[0]['date_fin'].isoformat()

        options_payload = []
        for candidate in sorted(year_candidates, key=lambda c: (c['date_fin'], c['date_paie'])):
            if cls._is_weekend(candidate['date_paie']) or cls._is_holiday(candidate['date_paie']):
                continue
            value = candidate['date_fin'].isoformat()
            label = candidate['date_fin'].isoformat()
            options_payload.append({
                'value': value,
                'label': label,
                'date_paie': candidate['date_paie'].isoformat() if candidate['date_paie'] else '',
                'mode': candidate['mode'],
                'selected': value == default_value,
            })

        if not options_payload:
            return [], None, 'Aucune date de paiement valide (lundi a vendredi, hors jours feries) pour l annee en cours.'

        if default_value not in {o['value'] for o in options_payload}:
            default_value = options_payload[0]['value']

        return options_payload, default_value, None

    def clean(self):
        cleaned_data = super().clean()
        employe = cleaned_data.get('employe')
        selected_date_fin = cleaned_data.get('periode_date_fin')

        if not employe:
            return cleaned_data

        # Ces credits sont saisis a la fiche employe et non dans la paie.
        cleaned_data['taux_horaire'] = employe.taux_horaire_defaut
        cleaned_data['montant_personnel_federal_td1'] = employe.montant_personnel_federal_defaut
        cleaned_data['montant_personnel_quebec_tp1015'] = employe.montant_personnel_quebec_defaut
        # J n'est pas le credit personnel de base TP-1015.
        # On le laisse a zero par defaut, sauf saisie explicite future.
        cleaned_data['deduction_tp1015_j'] = Decimal('0.00')

        # J1 demeure dans les calculs, mais doit toujours etre nul.
        cleaned_data['deduction_tp1016_j1'] = Decimal('0.00')

        cleaned_data['code_f_partie_remuneration_admissible'] = self._code_f_partie_total(cleaned_data)
        cleaned_data['deduction_code_f'] = self._code_f_total(cleaned_data)

        options_payload, default_value, error_message = self.options_fin_periode_annee_courante(employe)
        if error_message:
            self.add_error('employe', error_message)
            return cleaned_data

        selected_value = selected_date_fin or default_value
        candidate_by_value = {o['value']: o for o in options_payload}
        if selected_value not in candidate_by_value:
            self.add_error('periode_date_fin', 'Selection de fin de periode invalide.')
            return cleaned_data

        frequence = self._frequence_employe_ou_setting(employe)
        if not frequence:
            self.add_error('employe', 'Aucune frequence de paie configuree pour cet employe ni dans les parametres.')
            return cleaned_data

        selected_date = date_type.fromisoformat(selected_value)
        existing = (
            PeriodePaie.objects
            .filter(frequence_paie=frequence, date_fin=selected_date)
            .order_by('id')
            .first()
        )
        if existing and existing.fermee:
            self.add_error('periode_date_fin', 'Cette periode est fermee.')
            return cleaned_data
        if existing and Paie.objects.filter(employe=employe, periode=existing).exists():
            self.add_error('periode_date_fin', 'Une paie existe deja pour cet employe et cette periode.')
            return cleaned_data

        # Retrouver le candidat complet pour date_debut/date_paie.
        frequence_candidates = self._build_candidates_with_projection(employe, frequence)
        selected_candidate = next((c for c in frequence_candidates if c['date_fin'] == selected_date), None)
        if not selected_candidate:
            self.add_error('periode_date_fin', 'Impossible de determiner la periode correspondante.')
            return cleaned_data

        if self._is_weekend(selected_candidate['date_paie']) or self._is_holiday(selected_candidate['date_paie']):
            self.add_error('periode_date_fin', 'La date de paiement ne peut pas etre un samedi, un dimanche ou un jour ferie.')
            return cleaned_data

        self._selected_candidate = selected_candidate
        cleaned_data['periode_date_fin'] = selected_candidate['date_fin']
        cleaned_data['periode_date_paie'] = selected_candidate['date_paie']

        return cleaned_data

    def save(self, commit=True):
        if self._selected_candidate is None:
            raise ValidationError('Impossible de determiner la periode de paie pour cet employe.')

        if self._selected_candidate['mode'] == 'existing':
            periode = self._selected_candidate['periode']
        else:
            periode, _ = PeriodePaie.objects.get_or_create(
                frequence_paie=self._selected_candidate['frequence'],
                date_debut=self._selected_candidate['date_debut'],
                date_fin=self._selected_candidate['date_fin'],
                defaults={'date_paie': self._selected_candidate['date_paie']},
            )

        self.instance.periode = periode
        return super().save(commit=commit)
