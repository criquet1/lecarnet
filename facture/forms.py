import calendar

from django import forms
from django.forms import formset_factory

from .constants import MONTH_CHOICES_FR
from .models import Cheque, Tr_desc, Client, Fournisseur
from compte.models import Setting
from .utils import get_available_logos, is_expert


class ClientForm(forms.ModelForm):
    logo = forms.ChoiceField(label="Logo generique", required=False)
    logo_prive = forms.ImageField(
        label="Logo prive (televerse)",
        required=False,
        help_text=(
            "Facultatif. Si rempli, remplace le logo generique ci-dessus. Stocke dans la base "
            "de donnees de ce tenant -- visible seulement par ses utilisateurs."
        ),
    )
    logo_prive_effacer = forms.BooleanField(label="Retirer le logo prive actuel", required=False)

    class Meta:
        model = Client
        fields = ['nom', 'logo', 'comptes', 'active', 'afficher_card']
        labels = {
            'afficher_card': "Afficher sur la page d'accueil",
        }
        help_texts = {
            'afficher_card': (
                "Decoche pour un client occasionnel (facture seulement 1 ou 2 fois par annee, par exemple) : "
                "il n'apparaitra plus en carte, mais restera facturable depuis « Gerer les compagnies »."
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        logo_files = get_available_logos()

        self.fields['logo'].choices = [('', 'Par defaut (images.png)')] + [(name, name) for name in logo_files]
        self.fields['logo'].help_text = "Fichier pris depuis static/images/logos. Laisse vide pour images.png."
        if self.instance and self.instance.pk and self.instance.logo_prive:
            self.fields['logo_prive'].help_text += " Un logo prive est deja enregistre ; televerse un fichier pour le remplacer."
        else:
            self.fields.pop('logo_prive_effacer', None)
        # Le choix de compte(s) n'est obligatoire que pour un utilisateur expert :
        # un non-expert peut creer la compagnie sans le remplir, l'expert le
        # completera lors de sa verification (voir le jaune sur la carte).
        self.fields['comptes'].required = bool(user and is_expert(user))

    def clean_logo(self):
        return self.cleaned_data.get('logo') or 'images.png'

    def save(self, commit=True):
        instance = super().save(commit=False)
        uploaded = self.cleaned_data.get('logo_prive')
        if uploaded:
            instance.logo_prive = uploaded.read()
            instance.logo_prive_type = getattr(uploaded, 'content_type', None) or 'image/png'
        elif self.cleaned_data.get('logo_prive_effacer'):
            instance.logo_prive = None
            instance.logo_prive_type = None
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class FournisseurForm(forms.ModelForm):
    logo = forms.ChoiceField(label="Logo generique", required=False)
    logo_prive = forms.ImageField(
        label="Logo prive (televerse)",
        required=False,
        help_text=(
            "Facultatif. Si rempli, remplace le logo generique ci-dessus. Stocke dans la base "
            "de donnees de ce tenant -- visible seulement par ses utilisateurs."
        ),
    )
    logo_prive_effacer = forms.BooleanField(label="Retirer le logo prive actuel", required=False)

    class Meta:
        model = Fournisseur
        fields = ['nom', 'logo', 'comptes', 'active', 'afficher_card']
        labels = {
            'afficher_card': "Afficher sur la page d'accueil",
        }
        help_texts = {
            'afficher_card': (
                "Decoche pour un fournisseur occasionnel (facture recue seulement 1 ou 2 fois par annee, par exemple) : "
                "il n'apparaitra plus en carte, mais restera facturable depuis « Gerer les compagnies »."
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        logo_files = get_available_logos()

        self.fields['logo'].choices = [('', 'Par defaut (images.png)')] + [(name, name) for name in logo_files]
        self.fields['logo'].help_text = "Fichier pris depuis static/images/logos. Laisse vide pour images.png."
        if self.instance and self.instance.pk and self.instance.logo_prive:
            self.fields['logo_prive'].help_text += " Un logo prive est deja enregistre ; televerse un fichier pour le remplacer."
        else:
            self.fields.pop('logo_prive_effacer', None)
        # Le choix de compte(s) n'est obligatoire que pour un utilisateur expert :
        # un non-expert peut creer la compagnie sans le remplir, l'expert le
        # completera lors de sa verification (voir le jaune sur la carte).
        self.fields['comptes'].required = bool(user and is_expert(user))

    def clean_logo(self):
        return self.cleaned_data.get('logo') or 'images.png'

    def save(self, commit=True):
        instance = super().save(commit=False)
        uploaded = self.cleaned_data.get('logo_prive')
        if uploaded:
            instance.logo_prive = uploaded.read()
            instance.logo_prive_type = getattr(uploaded, 'content_type', None) or 'image/png'
        elif self.cleaned_data.get('logo_prive_effacer'):
            instance.logo_prive = None
            instance.logo_prive_type = None
        if commit:
            instance.save()
            self.save_m2m()
        return instance


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


class ChequeForm(forms.ModelForm):
    class Meta:
        model = Cheque
        fields = ['no_cheque', 'date_emission', 'client', 'fournisseur', 'montant', 'description']
        widgets = {
            'no_cheque': forms.TextInput(attrs={'class': 'form-control'}),
            'date_emission': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'client': forms.Select(attrs={'class': 'form-select'}),
            'fournisseur': forms.Select(attrs={'class': 'form-select'}),
            'montant': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'description': forms.TextInput(attrs={'class': 'form-control'}),
        }