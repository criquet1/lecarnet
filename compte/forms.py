import calendar
from datetime import date
from pathlib import Path

from django import forms
from django.conf import settings

from facture.models import Setting

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
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        logos_dir = Path(settings.BASE_DIR) / 'static' / 'images' / 'logos'
        allowed_ext = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg'}

        logo_files = []
        if logos_dir.exists():
            logo_files = sorted(
                p.name for p in logos_dir.iterdir()
                if p.is_file() and p.suffix.lower() in allowed_ext
            )

        if not logo_files:
            logo_files = ['images.png']

        self.fields['logo'].choices = [(name, name) for name in logo_files]
        self.fields['logo'].help_text = "Fichier pris depuis static/images/logos"
        self.fields['fin_annee_jour'].choices = self.DAY_CHOICES
        self.fields['fin_annee_mois'].choices = self.MONTH_CHOICES

        if self.instance and getattr(self.instance, 'annee_financiere', None):
            self.fields['fin_annee_jour'].initial = self.instance.annee_financiere.day
            self.fields['fin_annee_mois'].initial = self.instance.annee_financiere.month
        else:
            self.fields['fin_annee_jour'].initial = 31
            self.fields['fin_annee_mois'].initial = 12

    def clean(self):
        cleaned_data = super().clean()
        jour = cleaned_data.get('fin_annee_jour')
        mois = cleaned_data.get('fin_annee_mois')

        if jour and mois:
            max_day = calendar.monthrange(2000, int(mois))[1]
            if int(jour) > max_day:
                self.add_error('fin_annee_jour', f"Le mois choisi se termine au plus le {max_day}.")

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        mois = int(self.cleaned_data['fin_annee_mois'])
        jour = int(self.cleaned_data['fin_annee_jour'])
        instance.annee_financiere = date(2000, mois, jour)

        if commit:
            instance.save()

        return instance
