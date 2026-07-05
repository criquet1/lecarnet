import calendar

from django import forms
from django.forms import formset_factory

from .constants import MONTH_CHOICES_FR
from .models import Compagnie, Tr_desc
from compte.models import Setting
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
    fin_annee_jour = forms.TypedChoiceField(
        label="Jour",
        required=True,
        coerce=int,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    fin_annee_mois = forms.TypedChoiceField(
        label="Mois",
        required=True,
        coerce=int,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    MONTH_CHOICES = MONTH_CHOICES_FR

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
            'frequence_paie': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logo_files = get_available_logos()

        self.fields['logo'].choices = [(name, name) for name in logo_files]
        self.fields['logo'].help_text = "Fichier pris depuis static/images/logos"
        self.fields['fin_annee_jour'].choices = self.DAY_CHOICES
        self.fields['fin_annee_mois'].choices = self.MONTH_CHOICES

        if self.instance.pk:
            self.fields['fin_annee_jour'].initial = str(self.instance.fin_annee_jour) if self.instance.fin_annee_jour else '31'
            self.fields['fin_annee_mois'].initial = str(self.instance.fin_annee_mois) if self.instance.fin_annee_mois else '12'
        else:
            self.fields['fin_annee_jour'].initial = '31'
            self.fields['fin_annee_mois'].initial = '12'

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
        return super().save(commit=commit)


class TrDescForm(forms.ModelForm):
    class Meta:
        model = Tr_desc
        fields = ['date', 'desc_ctb', 'note_de_credit']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'note_de_credit': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
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