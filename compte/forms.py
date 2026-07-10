import re
from decimal import Decimal, InvalidOperation
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


class SettingForm(forms.ModelForm):
    logo = forms.ChoiceField(label="Logo", required=True)
    fin_annee_jour = forms.TypedChoiceField(label="Jour", required=False, coerce=int, empty_value=None)
    fin_annee_mois = forms.TypedChoiceField(label="Mois", required=False, coerce=int, empty_value=None)
    taux_cnesst_employeur = forms.CharField(required=False)
    taux_fss_employeur = forms.DecimalField(required=False, decimal_places=5, max_digits=7)
    comptes_paie_autres = forms.CharField(required=False, widget=forms.HiddenInput())

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
            'fin_annee_jour',
            'fin_annee_mois',
            'car',
            'cap',
            'compte_tps_percue',
            'compte_tps_payee',
            'compte_tvq_percue',
            'compte_tvq_payee',
            'compte_fr_retard',
            'taxes_mode',
            'frequence_paie',
            'date_debut_periode_paie_annee',
            'date_premier_paiement_paie_annee',
            'taux_cnesst_employeur',
            'taux_fss_employeur',
            'compte_salaires_a_payer',
            'compte_vacances_a_payer',
            'compte_das_federales',
            'compte_das_provinciales',
            'compte_salaire',
            'compte_vacances',
            'compte_benefices_marginaux',
            'comptes_paie_autres',
        ]
        widgets = {
            'nom': forms.TextInput(attrs={'class': 'form-control'}),
            'adresse': forms.TextInput(attrs={'class': 'form-control'}),
            'ville': forms.TextInput(attrs={'class': 'form-control'}),
            'code_postal': forms.TextInput(attrs={'class': 'form-control'}),
            'pays': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'frequence_paie': forms.Select(attrs={'class': 'form-select'}),
            'date_debut_periode_paie_annee': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'date_premier_paiement_paie_annee': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'car': forms.Select(attrs={'class': 'form-select'}),
            'cap': forms.Select(attrs={'class': 'form-select'}),
            'compte_tps_percue': forms.Select(attrs={'class': 'form-select'}),
            'compte_tps_payee': forms.Select(attrs={'class': 'form-select'}),
            'compte_tvq_percue': forms.Select(attrs={'class': 'form-select'}),
            'compte_tvq_payee': forms.Select(attrs={'class': 'form-select'}),
            'compte_fr_retard': forms.Select(attrs={'class': 'form-select'}),
            'taxes_mode': forms.Select(attrs={'class': 'form-select'}),
            'compte_salaires_a_payer': forms.Select(attrs={'class': 'form-select'}),
            'compte_vacances_a_payer': forms.Select(attrs={'class': 'form-select'}),
            'compte_das_federales': forms.Select(attrs={'class': 'form-select'}),
            'compte_das_provinciales': forms.Select(attrs={'class': 'form-select'}),
            'compte_salaire': forms.Select(attrs={'class': 'form-select'}),
            'compte_vacances': forms.Select(attrs={'class': 'form-select'}),
            'compte_benefices_marginaux': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logo_files = get_available_logos()

        self.fields['logo'].choices = [(name, name) for name in logo_files]
        self.fields['logo'].help_text = "Fichier pris depuis static/images/logos"
        self.fields['fin_annee_jour'].choices = [('', '--')] + self.DAY_CHOICES
        self.fields['fin_annee_mois'].choices = [('', '--')] + self.MONTH_CHOICES
        self.fields['fin_annee_jour'].widget.attrs.update({'class': 'form-select'})
        self.fields['fin_annee_mois'].widget.attrs.update({'class': 'form-select'})
        self.fields['taux_cnesst_employeur'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Ex.: 1,54',
            'inputmode': 'decimal',
        })
        self.fields['taux_cnesst_employeur'].help_text = 'Saisir le taux en $ par 100 $ de masse salariale (ex.: 1,54).'
        self.fields['taux_fss_employeur'].widget.attrs.update({
            'class': 'form-control',
            'step': '0.00001',
            'placeholder': 'Ex.: 0.01250',
            'inputmode': 'decimal',
        })
        self.fields['taux_fss_employeur'].help_text = 'Saisir le taux FSS en ratio (ex.: 0.01250 pour 1,25 %).'

        initial_autres = []
        if self.instance and self.instance.pk and self.instance.comptes_paie_autres:
            initial_autres = [str(value) for value in self.instance.comptes_paie_autres if value]
        self.initial['comptes_paie_autres'] = ','.join(initial_autres)

        if self.instance and self.instance.pk and self.instance.taux_cnesst_employeur is not None:
            display_value = (Decimal(self.instance.taux_cnesst_employeur) * Decimal('100')).quantize(Decimal('0.01'))
            self.initial['taux_cnesst_employeur'] = str(display_value)

    def clean_taux_cnesst_employeur(self):
        raw_value = (self.cleaned_data.get('taux_cnesst_employeur') or '').strip()
        if not raw_value:
            return None

        normalized = raw_value.replace('$', '').replace(' ', '').replace(',', '.')
        try:
            value_per_100 = Decimal(normalized)
        except (InvalidOperation, TypeError, ValueError):
            raise forms.ValidationError('Saisissez un nombre valide, par exemple 1,54.')

        if value_per_100 < 0:
            raise forms.ValidationError('Le taux CNESST doit etre positif.')

        if value_per_100 > Decimal('100'):
            raise forms.ValidationError('Le taux CNESST semble trop eleve.')

        # Stockage en ratio (ex.: 1,54 par 100$ devient 0,0154).
        return (value_per_100 / Decimal('100')).quantize(Decimal('0.00001'))

    def clean_comptes_paie_autres(self):
        raw_value = (self.cleaned_data.get('comptes_paie_autres') or '').strip()
        if not raw_value:
            return []

        entries = [part.strip() for part in raw_value.split(',') if part.strip()]
        try:
            account_ids = [int(value) for value in entries]
        except ValueError:
            raise forms.ValidationError('Les comptes additionnels de paie sont invalides.')

        unique_ids = list(dict.fromkeys(account_ids))
        existing_ids = set(Compte.objects.filter(numero__in=unique_ids).values_list('numero', flat=True))
        missing_ids = [str(value) for value in unique_ids if value not in existing_ids]
        if missing_ids:
            raise forms.ValidationError(
                f"Comptes introuvables: {', '.join(missing_ids)}"
            )
        return unique_ids

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.comptes_paie_autres = self.cleaned_data.get('comptes_paie_autres', [])
        if commit:
            instance.save()
            self.save_m2m()
        return instance


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

