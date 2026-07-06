import re
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

