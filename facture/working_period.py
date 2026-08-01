import re
from datetime import date

from facture.constants import MONTH_LABELS_FR


SESSION_KEY = 'working_periods'
COOKIE_MAX_AGE = 365 * 24 * 60 * 60


def _tenant_key(request):
    return getattr(request, 'active_client_alias', None) or 'default'


def working_period_cookie_name(request):
    tenant_key = re.sub(r'[^a-zA-Z0-9_-]', '_', _tenant_key(request))
    return f'working_period_{tenant_key}'


def parse_working_period(value):
    match = re.fullmatch(r'(\d{4})-(\d{2})', str(value or '').strip())
    if not match:
        return None

    year = int(match.group(1))
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return year, month


def get_working_period(request, today=None):
    reference_date = today or date.today()
    tenant_key = _tenant_key(request)
    session = getattr(request, 'session', {})
    periods = session.get(SESSION_KEY, {}) if hasattr(session, 'get') else {}
    raw_value = periods.get(tenant_key)

    if not parse_working_period(raw_value):
        raw_value = request.COOKIES.get(working_period_cookie_name(request))

    parsed = parse_working_period(raw_value)
    if parsed:
        year, month = parsed
    else:
        year, month = reference_date.year, reference_date.month

    return {
        'year': year,
        'month': month,
        'value': f'{year:04d}-{month:02d}',
        'label': f'{MONTH_LABELS_FR[month]} {year}',
        'tenant_key': tenant_key,
    }


def set_working_period(request, value):
    parsed = parse_working_period(value)
    if not parsed:
        return None

    year, month = parsed
    normalized_value = f'{year:04d}-{month:02d}'
    periods = dict(request.session.get(SESSION_KEY, {}))
    periods[_tenant_key(request)] = normalized_value
    request.session[SESSION_KEY] = periods
    request.session.modified = True
    return normalized_value