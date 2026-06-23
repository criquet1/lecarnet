from django import forms
from .models import Compagnie, Facture, DetailFacture

class CompagnieForm(forms.ModelForm):
    class Meta:
        model = Compagnie
        fields = ['nom', 'logo', 'comptes', 'cap_ou_car']


class FactureForm(forms.ModelForm):
    date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        initial=forms.fields.datetime.date.today
    )

    class Meta:
        model = Facture
        fields = ['numero', 'date', 'compagnie', 'total']


class DetailFactureForm(forms.ModelForm):
    class Meta:
        model = DetailFacture
        fields = ['compte', 'montant']
        widgets = {
            'montant': forms.NumberInput(attrs={'placeholder': '0.00', 'style': 'text-align: right;'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['compte'].required = False
        self.fields['montant'].required = False

    def clean(self):
        cleaned_data = super().clean()
        compte = cleaned_data.get('compte')
        montant = cleaned_data.get('montant')

        # Ligne valide si complètement vide (permet les lignes optionnelles)
        if not compte and montant is None:
            return cleaned_data

        # Ligne valide si complètement remplie
        if compte and montant is not None:
            return cleaned_data

        # Sinon, erreur: ligne partiellement remplie
        if compte and montant is None:
            raise forms.ValidationError("Veuillez saisir un montant pour ce compte.")
        if not compte and montant is not None:
            raise forms.ValidationError("Veuillez sélectionner un compte pour ce montant.")