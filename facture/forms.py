import calendar
from datetime import date

from django import forms
from django.forms import formset_factory

from .models import Compagnie, Setting, Tr_desc
from .utils import get_available_logos


class CompagnieForm(forms.ModelForm):
    logo = forms.ChoiceField(label="Logo", required=True)

    class Meta:
        model = Compagnie
        fields = ['nom', 'logo', 'comptes', 'cap_ou_car']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logo_files = get_available_logos()

        self.fields['logo'].choices = [(name, name) for name in logo_files]
        self.fields['logo'].help_text = "Fichier pris depuis static/images/logos"


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
        logo_files = get_available_logos()

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


class TrDescForm(forms.ModelForm):
    class Meta:
        model = Tr_desc
        fields = ['date', 'description']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
        }


class TrDetailForm(forms.Form):
    compte = forms.ModelChoiceField(queryset=None, required=False, empty_label="Choisissez un compte")
    montant = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={
            'class': 'form-control text-end',
            'placeholder': '0.00',
            'step': '0.01',
        }),
    )

    def __init__(self, *args, **kwargs):
        comptes_queryset = kwargs.pop('comptes_queryset', None)
        super().__init__(*args, **kwargs)
        if comptes_queryset is not None:
            self.fields['compte'].queryset = comptes_queryset

    def clean(self):
        cleaned_data = super().clean()
        compte = cleaned_data.get('compte')
        montant = cleaned_data.get('montant')

        if montant in (None, ''):
            return cleaned_data

        if not compte:
            raise forms.ValidationError("Chaque ligne avec un montant doit contenir un compte.")

        return cleaned_data


TrDetailFormSet = formset_factory(TrDetailForm, extra=25)