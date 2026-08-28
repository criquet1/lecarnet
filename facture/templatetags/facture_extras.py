from decimal import Decimal, ROUND_HALF_UP

from django import template

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
