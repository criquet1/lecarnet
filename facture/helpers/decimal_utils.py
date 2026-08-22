from decimal import Decimal

def coerce_decimal(value):
    """
    Convertit proprement une valeur en Decimal.
    """
    if value is None or value == '':
        return Decimal('0')
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
