from decimal import Decimal, ROUND_HALF_UP

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


def _grouper_milliers(entier):
    """Regroupe une chaine de chiffres par blocs de 3 en partant de la droite."""
    groupes = []
    while len(entier) > 3:
        groupes.insert(0, entier[-3:])
        entier = entier[:-3]
    groupes.insert(0, entier)
    return ' '.join(groupes)


def _decouper_montant(value):
    """Retourne (signe, partie_entiere_groupee, decimales) pour un montant,
    selon la convention quebecoise : espace pour les milliers, 2 decimales."""
    quantized = Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    negatif = quantized < 0
    entier, _, decimales = f'{abs(quantized):.2f}'.partition('.')
    return ('-' if negatif else ''), _grouper_milliers(entier), decimales


@register.filter
def montant(value):
    """Formate un montant selon la convention quebecoise : espace pour les
    milliers, virgule pour les decimales (ex. : 1000.99 -> '1 000,99')."""
    if value is None or value == '':
        return ''
    signe, entier, decimales = _decouper_montant(value)
    return f'{signe}{entier},{decimales}'


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key, [])


@register.filter
def logo_static_path(value):
    logo = (value or '').strip()
    if not logo:
        return 'images/logos/images.png'
    if '/' in logo:
        return logo
    return f'images/logos/{logo}'


@register.filter
def in_group(user, group_name):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return user.groups.filter(name__iexact=(group_name or '').strip()).exists()


@register.filter
def has_interets_access(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser or user.groups.filter(name__iexact='expert').exists():
        return True
    return user.groups.filter(name__iexact='acces_interets').exists()


@register.filter
def accounting_amount(value):
    """Comme `montant`, mais affiche les montants negatifs entre parentheses,
    convention comptable (ex. : -1000.99 -> '(1 000,99)')."""
    if value is None:
        return ''
    _, entier, decimales = _decouper_montant(value)
    formatted = f'{entier},{decimales}'
    if value < 0:
        return f'({formatted})'
    return formatted


def _montant_html(value, comptable):
    """Construit le HTML d'un montant decoupe en deux <span> (partie entiere /
    partie decimale) pour permettre l'alignement de la virgule en CSS (grid),
    peu importe le nombre de chiffres ou la presence de parentheses."""
    if value is None or value == '':
        return ''
    signe, entier, decimales = _decouper_montant(value)
    negatif = comptable and value < 0
    partie_entiere = f'({entier}' if negatif else f'{signe}{entier}'
    partie_decimale = f'{decimales})' if negatif else decimales
    return mark_safe(
        '<span class="montant-cell">'
        f'<span class="mnt-int">{partie_entiere}</span>'
        f'<span class="mnt-dec">,{partie_decimale}</span>'
        '</span>'
    )


@register.filter
def montant_aligne(value):
    """Comme `montant`, mais rend deux <span> (mnt-int / mnt-dec) pour que la
    virgule s'aligne verticalement via la classe CSS .montant-cell."""
    return _montant_html(value, comptable=False)


@register.filter
def accounting_amount_aligne(value):
    """Comme `accounting_amount`, mais rend deux <span> (mnt-int / mnt-dec)
    pour que la virgule s'aligne verticalement via la classe CSS .montant-cell."""
    return _montant_html(value, comptable=True)
