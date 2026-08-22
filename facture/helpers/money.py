from decimal import Decimal

def money(value):
    """
    Normalise un montant en Decimal avec deux décimales.
    """
    if value is None or value == '':
        return Decimal('0')
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
