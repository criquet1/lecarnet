from pathlib import Path

from django import forms
from django.conf import settings
from django.forms import formset_factory

from .models import Compagnie, Tr_desc


class CompagnieForm(forms.ModelForm):
    logo = forms.ChoiceField(label="Logo", required=True)

    class Meta:
        model = Compagnie
        fields = ['nom', 'logo', 'comptes', 'cap_ou_car']

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


class TrDescForm(forms.ModelForm):
    class Meta:
        model = Tr_desc
        fields = ['date', 'description']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
        }


class TrDetailForm(forms.Form):
    compte = forms.ModelChoiceField(queryset=None, required=False, empty_label="Choisir un compte")
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