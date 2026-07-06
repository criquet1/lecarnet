from django import forms
from django.contrib.auth import get_user_model
from django.utils.text import slugify

from .models import ClientDatabase, Societe, UserClientAccess, UserSocieteAccess


class SocieteForm(forms.ModelForm):
    class Meta:
        model = Societe
        fields = [
            'name',
            'slug',
            'adresse',
            'telephone',
            'email',
            'personne_ressource',
            'site_web',
            'notes',
            'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex: Societe A'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'societe-a'}),
            'adresse': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '123 Rue Exemple, Montreal'}),
            'telephone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '514-555-1234'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'contact@societe-a.com'}),
            'personne_ressource': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nom de la personne ressource'}),
            'site_web': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://www.societe-a.com'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Notes internes (optionnel)'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_slug(self):
        value = (self.cleaned_data.get('slug') or '').strip().lower()
        if not value:
            name = (self.cleaned_data.get('name') or '').strip()
            value = slugify(name)

        if not value:
            raise forms.ValidationError('Impossible de generer un slug valide.')

        return value


class SocieteUserCreateForm(forms.Form):
    societe = forms.ModelChoiceField(
        label='Societe',
        queryset=Societe.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    username = forms.CharField(
        label='Nom d utilisateur',
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'utilisateur.societe'}),
    )
    email = forms.EmailField(
        label='Email',
        required=False,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'user@exemple.com'}),
    )
    temp_password = forms.CharField(
        label='Mot de passe temporaire',
        min_length=8,
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Mot de passe temporaire'}),
        help_text='L utilisateur devra changer ce mot de passe a la premiere connexion.',
    )
    is_default = forms.BooleanField(
        label='Definir cette societe comme societe par defaut',
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    is_expert = forms.BooleanField(
        label='Role Expert (coche = attribuer, decoche = retirer)',
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def __init__(self, *args, **kwargs):
        societes_qs = kwargs.pop('societes_qs', None)
        super().__init__(*args, **kwargs)
        if societes_qs is None:
            societes_qs = Societe.objects.filter(is_active=True).order_by('name', 'id')
        self.fields['societe'].queryset = societes_qs

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip().lower()
        if not username:
            raise forms.ValidationError('Le nom d utilisateur est requis.')

        user_model = get_user_model()
        if user_model.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('Ce nom d utilisateur existe deja.')

        return username

    def save(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username=self.cleaned_data['username'],
            email=(self.cleaned_data.get('email') or '').strip(),
            password=self.cleaned_data['temp_password'],
        )

        societe = self.cleaned_data['societe']
        is_default = bool(self.cleaned_data.get('is_default'))

        if is_default:
            UserSocieteAccess.objects.filter(user=user, is_default=True).update(is_default=False)

        UserSocieteAccess.objects.update_or_create(
            user=user,
            societe=societe,
            defaults={'is_default': is_default},
        )

        return user


class SocieteUserAssignForm(forms.Form):
    user = forms.ModelChoiceField(
        label='Utilisateur existant',
        queryset=get_user_model().objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    societe = forms.ModelChoiceField(
        label='Societe',
        queryset=Societe.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    is_default = forms.BooleanField(
        label='Definir cette societe comme societe par defaut',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    is_expert = forms.BooleanField(
        label='Role Expert (coche = attribuer, decoche = retirer)',
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def __init__(self, *args, **kwargs):
        societes_qs = kwargs.pop('societes_qs', None)
        users_qs = kwargs.pop('users_qs', None)
        super().__init__(*args, **kwargs)

        if societes_qs is None:
            societes_qs = Societe.objects.filter(is_active=True).order_by('name', 'id')
        if users_qs is None:
            users_qs = get_user_model().objects.order_by('username', 'id')

        self.fields['societe'].queryset = societes_qs
        self.fields['user'].queryset = users_qs

    def save(self):
        user = self.cleaned_data['user']
        societe = self.cleaned_data['societe']
        is_default = bool(self.cleaned_data.get('is_default'))

        if is_default:
            UserSocieteAccess.objects.filter(user=user, is_default=True).update(is_default=False)

        access, _ = UserSocieteAccess.objects.update_or_create(
            user=user,
            societe=societe,
            defaults={'is_default': is_default},
        )

        if not UserSocieteAccess.objects.filter(user=user, is_default=True).exists():
            access.is_default = True
            access.save(update_fields=['is_default'])

        return access


class ExpertSocieteUserCreateForm(forms.Form):
    username = forms.CharField(
        label='Nom d utilisateur',
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'utilisateur.societe'}),
    )
    email = forms.EmailField(
        label='Email',
        required=False,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'user@exemple.com'}),
    )
    temp_password = forms.CharField(
        label='Mot de passe temporaire',
        min_length=8,
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Mot de passe temporaire'}),
        help_text='L utilisateur devra changer ce mot de passe a la premiere connexion.',
    )
    is_expert = forms.BooleanField(
        label='Role Expert',
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip().lower()
        if not username:
            raise forms.ValidationError('Le nom d utilisateur est requis.')

        user_model = get_user_model()
        if user_model.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('Ce nom d utilisateur existe deja.')

        return username

    def save(self, societe):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username=self.cleaned_data['username'],
            email=(self.cleaned_data.get('email') or '').strip(),
            password=self.cleaned_data['temp_password'],
        )

        UserSocieteAccess.objects.update_or_create(
            user=user,
            societe=societe,
            defaults={'is_default': True},
        )

        return user


class ExpertUserTenantAssignForm(forms.Form):
    user = forms.ModelChoiceField(
        label='Utilisateur',
        queryset=get_user_model().objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    tenant = forms.ModelChoiceField(
        label='Tenant',
        queryset=ClientDatabase.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    is_default = forms.BooleanField(
        label='Definir ce tenant comme tenant par defaut',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def __init__(self, *args, **kwargs):
        users_qs = kwargs.pop('users_qs', None)
        tenants_qs = kwargs.pop('tenants_qs', None)
        super().__init__(*args, **kwargs)

        if users_qs is None:
            users_qs = get_user_model().objects.none()
        if tenants_qs is None:
            tenants_qs = ClientDatabase.objects.none()

        self.fields['user'].queryset = users_qs
        self.fields['tenant'].queryset = tenants_qs

    def save(self):
        user = self.cleaned_data['user']
        tenant = self.cleaned_data['tenant']
        is_default = bool(self.cleaned_data.get('is_default'))

        if is_default:
            UserClientAccess.objects.filter(user=user, is_default=True).update(is_default=False)

        access, _ = UserClientAccess.objects.update_or_create(
            user=user,
            client=tenant,
            defaults={'is_default': is_default},
        )

        if not UserClientAccess.objects.filter(user=user, is_default=True).exists():
            access.is_default = True
            access.save(update_fields=['is_default'])

        return access
