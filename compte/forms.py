import calendar
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from .models import BulletinPaie
from django import forms
from django.contrib.auth import get_user_model

from compte.models import Setting
from facture.utils import get_available_logos
from tenancy.models import Societe

from .models import Compte, SoldeAuxLivres


class CompteForm(forms.ModelForm):
    class Meta:
        model = Compte
        fields = ['numero', 'libelle', 'no_total']
        widgets = {
            'numero': forms.NumberInput(attrs={'class': 'form-control'}),
            'libelle': forms.TextInput(attrs={'class': 'form-control'}),
            'no_total': forms.Select(attrs={'class': 'form-select'}),
        }


class SoldeAuxLivresForm(forms.ModelForm):
    class Meta:
        model = SoldeAuxLivres
        fields = ['compte', 'solde_depart']
        widgets = {
            'compte': forms.Select(attrs={'class': 'form-select'}),
            'solde_depart': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }


class CompteCsvImportForm(forms.Form):
    csv_file = forms.FileField(
        label='Fichier CSV',
        help_text='Colonnes acceptees: compte_no/compte_libelle/compte_total ou numero/libelle/no_total.',
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.csv,text/csv'}),
    )

    def clean_csv_file(self):
        csv_file = self.cleaned_data['csv_file']
        filename = (csv_file.name or '').lower()
        if not filename.endswith('.csv'):
            raise forms.ValidationError('Le fichier doit etre au format .csv')
        if csv_file.size == 0:
            raise forms.ValidationError('Le fichier est vide.')
        return csv_file


class ImpQuebecForm(forms.Form):
    P = forms.IntegerField(
        label="P - Nombre de periodes de paie dans l'annee",
        min_value=1,
        required=True,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '1', 'placeholder': 'Ex.: 26'}),
    )
    G = forms.DecimalField(
        label="G - Remuneration brute assujettie (periode de paie)",
        min_value=0,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}),
    )
    F = forms.DecimalField(
        label="F - Total des sommes deduites",
        min_value=0,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}),
    )

    FA = forms.DecimalField(label='FA - Cotisation a un RPA', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FB = forms.DecimalField(label='FB - Cotisation a un REER', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FC = forms.DecimalField(label='FC - Cotisation a un RVER ou RPAC', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FD = forms.DecimalField(label='FD - Cotisation a un CELIAPP', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FE = forms.DecimalField(label='FE - Cotisation a une convention de retraite', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FF = forms.DecimalField(label='FF - Deduction relative au RIC', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FG = forms.DecimalField(label='FG - Deduction voyages region eloignee reconnue', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FH = forms.DecimalField(label="FH - Deduction pour option d'achat de titres (somme FH1 à FH10)", min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'readonly': 'readonly', 'tabindex': '-1'}))

    FH1 = forms.DecimalField(label='FH1 - Revenus situes dans une reserve', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FH2 = forms.DecimalField(label='FH2 - Deduction pour specialiste etranger', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FH3 = forms.DecimalField(label='FH3 - Deduction pour chercheur etranger', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FH4 = forms.DecimalField(label='FH4 - Chercheur etranger en stage postdoctoral', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FH5 = forms.DecimalField(label='FH5 - Deduction pour expert etranger', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FH6 = forms.DecimalField(label='FH6 - Deduction pour professeur etranger', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FH7 = forms.DecimalField(label='FH7 - Producteur etranger ou poste cle au Quebec', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FH8 = forms.DecimalField(label='FH8 - Travailleur agricole etranger', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FH9 = forms.DecimalField(label='FH9 - Forces canadiennes et forces policieres', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    FH10 = forms.DecimalField(label='FH10 - Autres deductions admissibles', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))

    D = forms.DecimalField(
        label='D - Salaire brut assujetti (periode de paie)',
        min_value=0,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}),
    )
    H = forms.DecimalField(
        label='H - Deduction pour travailleur (periode)',
        min_value=0,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}),
    )

    CS = forms.DecimalField(label='CS - Cotisations supplementaires RRQ', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    C = forms.DecimalField(label='C - Cotisation RRQ (base + 1re supp.)', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    C2 = forms.DecimalField(label='C2 - 2e cotisation supplementaire RRQ', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    S3 = forms.DecimalField(label='S3 - Salaire admissible RRQ', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    B2 = forms.DecimalField(label='B2 - Montants forfaitaires verses', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))

    J = forms.DecimalField(label='J - Deduction ligne 19 (TP-1015.3)', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    J3 = forms.DecimalField(label='J3 - Valeur annuelle ligne 19', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    PR = forms.IntegerField(label='PR - Nombre de periodes restantes', min_value=0, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '1', 'placeholder': '0'}))
    J1 = forms.DecimalField(label='J1 - Deduction annuelle autorisee (TP-1016)', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    J2 = forms.DecimalField(label='J2 - Valeur annuelle TP-1016', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))

    I = forms.DecimalField(label='I - Revenu imposable annuel', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    T = forms.DecimalField(label="T - Taux d'imposition applique", min_value=0, decimal_places=4, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.0001', 'placeholder': 'Ex.: 0.1400'}))
    K = forms.DecimalField(label='K - Constante de rajustement', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    K1 = forms.DecimalField(label='K1 - Credits non remboursables autorises (TP-1016)', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    K2 = forms.DecimalField(label='K2 - Credits non remboursables annuels autorises', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    E = forms.DecimalField(label='E - Credits personnels (E1 + E2)', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    E1 = forms.DecimalField(label='E1 - Valeur indexee des credits personnels', min_value=0, decimal_places=2, required=False, initial=Decimal('18952.00'), widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    E2 = forms.DecimalField(label='E2 - Valeur non indexee des credits personnels', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    Q = forms.DecimalField(label='Q - Somme retenue Fonds de solidarite FTQ (periode)', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    Q1 = forms.DecimalField(label='Q1 - Somme retenue Fondaction (periode)', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    Y = forms.DecimalField(label="Y - Impot pour l'annee", min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    L = forms.DecimalField(label='L - Retenue supplementaire demandee', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))
    A = forms.DecimalField(label='A - Impot a retenir pour la periode de paie', min_value=0, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}))

    def clean(self):
        cleaned_data = super().clean()

        money_q = Decimal('0.01')
        rate_q = Decimal('0.0001')

        def as_decimal(field_name, default='0'):
            value = cleaned_data.get(field_name)
            if value in (None, ''):
                return Decimal(default)
            if isinstance(value, Decimal):
                return value
            try:
                return Decimal(str(value))
            except (InvalidOperation, ValueError):
                return Decimal(default)

        def q_money(value):
            return value.quantize(money_q, rounding=ROUND_HALF_UP)

        def q_rate(value):
            return value.quantize(rate_q, rounding=ROUND_HALF_UP)

        p = Decimal(cleaned_data.get('P') or 0)
        g = as_decimal('G')
        d = as_decimal('D')
        c = as_decimal('C')
        c2 = as_decimal('C2')
        s3 = as_decimal('S3')
        b2 = as_decimal('B2')
        j3 = as_decimal('J3')
        j2 = as_decimal('J2')
        pr = Decimal(cleaned_data.get('PR') or 0)
        t = as_decimal('T')
        k = as_decimal('K')
        k1_input = as_decimal('K1')
        k2 = as_decimal('K2')
        e1 = as_decimal('E1')
        e2 = as_decimal('E2')
        q = as_decimal('Q')
        q1 = as_decimal('Q1')
        l = as_decimal('L')

        fh = sum(
            (as_decimal(name) for name in ['FH1', 'FH2', 'FH3', 'FH4', 'FH5', 'FH6', 'FH7', 'FH8', 'FH9', 'FH10']),
            Decimal('0'),
        )
        cleaned_data['FH'] = q_money(fh)

        f = sum((as_decimal(name) for name in ['FA', 'FB', 'FC', 'FD', 'FE', 'FF', 'FG']), Decimal('0')) + fh

        d = g
        s3 = g
        if cleaned_data.get('B2') in (None, ''):
            b2 = Decimal('0')
        if p > 0:
            rrq_base = max((s3 - b2) - (Decimal('3500') / p), Decimal('0'))
            c = rrq_base * Decimal('0.0630')
        else:
            c = Decimal('0')

        h_max = (Decimal('1450') / p) if p > 0 else Decimal('0')
        h = min(Decimal('0.06') * d, h_max)

        cs = (c * (Decimal('0.01') / Decimal('0.0630'))) + c2
        csa = (cs * ((s3 - b2) / s3)) if s3 > 0 else Decimal('0')

        j = ((p * j3) / pr) if pr > 0 else Decimal('0')
        j1 = ((p * j2) / pr) if pr > 0 else Decimal('0')

        e = e1 + e2
        i = (p * (g - f - h - csa)) - j - j1

        # Determiner automatiquement T et K selon les tranches affichees dans la page.
        i_for_bracket = max(i, Decimal('0'))
        if i_for_bracket <= Decimal('54345'):
            t = Decimal('0.14')
            k = Decimal('0')
        elif i_for_bracket <= Decimal('108680'):
            t = Decimal('0.19')
            k = Decimal('2717')
        elif i_for_bracket <= Decimal('132245'):
            t = Decimal('0.24')
            k = Decimal('8151')
        else:
            t = Decimal('0.2575')
            k = Decimal('10465')

        # Si K2 est fourni avec PR, convertir en K1 (meme principe que J/J1).
        k1 = ((p * k2) / pr) if (k2 > 0 and pr > 0) else k1_input

        annual_worker_funds = p * (q + q1)
        if annual_worker_funds > Decimal('5000'):
            self.add_error('Q1', 'Le total annuel des fonds de travailleurs (P x (Q + Q1)) ne doit pas depasser 5 000 $.')

        y = (t * i) - k - k1 - (Decimal('0.14') * e) - (Decimal('0.15') * p * q) - (Decimal('0.15') * p * q1)
        y = max(y, Decimal('0'))
        a = ((y / p) + l) if p > 0 else l

        cleaned_data['F'] = q_money(f)
        cleaned_data['D'] = q_money(d)
        cleaned_data['C'] = q_money(c)
        cleaned_data['S3'] = q_money(s3)
        cleaned_data['B2'] = q_money(b2)
        cleaned_data['H'] = q_money(h)
        cleaned_data['CS'] = q_money(cs)
        cleaned_data['J'] = q_money(j)
        cleaned_data['J1'] = q_money(j1)
        cleaned_data['E'] = q_money(e)
        cleaned_data['I'] = q_money(i)
        cleaned_data['Y'] = q_money(y)
        cleaned_data['A'] = q_money(a)
        cleaned_data['K'] = q_money(k)
        cleaned_data['K1'] = q_money(k1)
        cleaned_data['T'] = q_rate(t)

        return cleaned_data


class SettingForm(forms.ModelForm):
    logo = forms.ChoiceField(label="Logo", required=True)
    fin_annee_jour = forms.ChoiceField(label="Jour", required=True)
    fin_annee_mois = forms.ChoiceField(label="Mois", required=True)

    MONTH_CHOICES = [
        ('1', 'Janvier'),
        ('2', 'Février'),
        ('3', 'Mars'),
        ('4', 'Avril'),
        ('5', 'Mai'),
        ('6', 'Juin'),
        ('7', 'Juillet'),
        ('8', 'Août'),
        ('9', 'Septembre'),
        ('10', 'Octobre'),
        ('11', 'Novembre'),
        ('12', 'Décembre'),
    ]

    DAY_CHOICES = [(str(day), str(day)) for day in range(1, 32)]

    class Meta:
        model = Setting
        fields = [
            'nom',
            'logo',
            'adresse',
            'ville',
            'code_postal',
            'pays',
            'phone',
            'email',
            'car',
            'cap',
            'compte_tps_percue',
            'compte_tps_payee',
            'compte_tvq_percue',
            'compte_tvq_payee',
            'compte_fr_retard',
            'taxes_mode',
        ]
        widgets = {
            'nom': forms.TextInput(attrs={'class': 'form-control'}),
            'adresse': forms.TextInput(attrs={'class': 'form-control'}),
            'ville': forms.TextInput(attrs={'class': 'form-control'}),
            'code_postal': forms.TextInput(attrs={'class': 'form-control'}),
            'pays': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'car': forms.Select(attrs={'class': 'form-select'}),
            'cap': forms.Select(attrs={'class': 'form-select'}),
            'compte_tps_percue': forms.Select(attrs={'class': 'form-select'}),
            'compte_tps_payee': forms.Select(attrs={'class': 'form-select'}),
            'compte_tvq_percue': forms.Select(attrs={'class': 'form-select'}),
            'compte_tvq_payee': forms.Select(attrs={'class': 'form-select'}),
            'compte_fr_retard': forms.Select(attrs={'class': 'form-select'}),
            'taxes_mode': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logo_files = get_available_logos()

        self.fields['logo'].choices = [(name, name) for name in logo_files]
        self.fields['logo'].help_text = "Fichier pris depuis static/images/logos"
        self.fields['fin_annee_jour'].choices = self.DAY_CHOICES
        self.fields['fin_annee_mois'].choices = self.MONTH_CHOICES


class BulletinPaieForm(forms.ModelForm):
    class Meta:
        model = BulletinPaie
        # Liste des champs que le gestionnaire doit remplir manuellement
        fields = [
            'employe_nom',
            'heures_travaillees',
            'taux_horaire',
            'periodes_par_annee',
            'montant_personnel_federal_td1',
            'montant_personnel_quebec_tp1015',
            'cumul_brut_annee',
            'cumul_rrq_annee',
            'cumul_rqap_annee',
            'cumul_ae_annee',
        ]
        
        # Ajout de labels clairs en français pour l'interface
        labels = {
            'employe_nom': "Nom de l'employé",
            'heures_travaillees': "Nombre d'heures travaillées",
            'taux_horaire': "Salaire horaire ($ / heure)",
            'periodes_par_annee': "Fréquence de paie (périodes par an)",
            'montant_personnel_federal_td1': "Crédit d'impôt fédéral - TD1 (0 pour montant de base)",
            'montant_personnel_quebec_tp1015': "Crédit d'impôt provincial - TP-1015.3 (0 pour montant de base)",
            'cumul_brut_annee': "Cumul du salaire brut cette année",
            'cumul_rrq_annee': "Cumul RRQ déjà prélevé cette année",
            'cumul_rqap_annee': "Cumul RQAP déjà prélevé cette année",
            'cumul_ae_annee': "Cumul Assurance-Emploi déjà prélevé cette année",
        }

        # Configuration des champs HTML pour intégrer du style (ex: Bootstrap)
        widgets = {
            'employe_nom': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex: Jean Tremblay'}),
            'heures_travaillees': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'taux_horaire': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'periodes_par_annee': forms.NumberInput(attrs={'class': 'form-control'}),
            'montant_personnel_federal_td1': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'montant_personnel_quebec_tp1015': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'cumul_brut_annee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'cumul_rrq_annee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'cumul_rqap_annee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'cumul_ae_annee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }

    def clean_heures_travaillees(self):
        """S'assure que les heures ne sont pas négatives."""
        heures = self.cleaned_data.get('heures_travaillees')
        if heures < 0:
            raise forms.ValidationError("Le nombre d'heures ne peut pas être inférieur à zéro.")
        return heures

    def clean_taux_horaire(self):
        """S'assure que le salaire horaire n'est pas négatif."""
        taux = self.cleaned_data.get('taux_horaire')
        if taux < 0:
            raise forms.ValidationError("Le taux horaire ne peut pas être négatif.")
        return taux


class CreerTenantForm(forms.Form):
    name = forms.CharField(
        label='Nom du client',
        max_length=120,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex: Client Delta'}),
    )
    slug = forms.CharField(
        label='Slug client',
        max_length=50,
        help_text="Lettres minuscules, chiffres et tirets (ex: client-delta).",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'client-delta'}),
    )
    societe = forms.ModelChoiceField(
        label='Societe',
        queryset=Societe.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    db_alias = forms.CharField(
        label='Alias de base',
        max_length=50,
        help_text="Doit correspondre a la cle tenant (ex: client_delta).",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'client_delta'}),
    )
    db_name = forms.CharField(
        label='Nom de la base de donnees',
        max_length=63,
        required=False,
        help_text='Optionnel. Si vide, lecarnet_<alias> sera utilise.',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'lecarnet_client_delta'}),
    )
    username = forms.CharField(
        label='Nom d utilisateur',
        max_length=150,
        help_text='Compte cree automatiquement dans la base centrale.',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'utilisateur.client'}),
    )
    temp_password = forms.CharField(
        label='Mot de passe temporaire',
        min_length=8,
        help_text='Ce mot de passe sera exige au premier login, puis l utilisateur devra le modifier.',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Mot de passe temporaire'}),
    )

    def __init__(self, *args, **kwargs):
        societes_qs = kwargs.pop('societes_qs', None)
        fixed_societe = kwargs.pop('fixed_societe', None)
        super().__init__(*args, **kwargs)
        societes = societes_qs if societes_qs is not None else Societe.objects.filter(is_active=True).order_by('name', 'id')
        self.fields['societe'].queryset = societes
        self.fixed_societe = fixed_societe

        if self.fixed_societe is not None:
            self.fields['societe'].queryset = Societe.objects.filter(pk=self.fixed_societe.pk)
            self.fields['societe'].initial = self.fixed_societe.pk
            self.fields['societe'].required = False
            self.fields['societe'].widget = forms.HiddenInput()

    def clean_societe(self):
        if self.fixed_societe is not None:
            return self.fixed_societe

        societe = self.cleaned_data.get('societe')
        if societe is None:
            raise forms.ValidationError('La societe est requise.')
        return societe

    def clean_slug(self):
        value = (self.cleaned_data.get('slug') or '').strip().lower()
        if not re.fullmatch(r'[a-z0-9-]+', value):
            raise forms.ValidationError('Utiliser uniquement a-z, 0-9 et - pour le slug.')
        return value

    def clean_db_alias(self):
        value = (self.cleaned_data.get('db_alias') or '').strip().lower()
        if not re.fullmatch(r'[a-z0-9_]+', value):
            raise forms.ValidationError('Utiliser uniquement a-z, 0-9 et _ pour l\'alias.')
        return value

    def clean_db_name(self):
        value = (self.cleaned_data.get('db_name') or '').strip()
        if not value:
            return value
        if not re.fullmatch(r'[A-Za-z0-9_]+', value):
            raise forms.ValidationError('Utiliser uniquement lettres, chiffres et _ pour le nom de base.')
        return value

    def clean_username(self):
        value = (self.cleaned_data.get('username') or '').strip().lower()
        if not value:
            raise forms.ValidationError('Le nom d utilisateur est requis.')

        user_model = get_user_model()
        if user_model.objects.filter(username__iexact=value).exists():
            raise forms.ValidationError('Ce nom d utilisateur existe deja.')

        return value

