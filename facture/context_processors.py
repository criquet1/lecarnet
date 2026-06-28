import calendar
from datetime import date

from facture.models import Setting


MONTH_LABELS_FR = {
    1: 'Janvier',
    2: 'Fevrier',
    3: 'Mars',
    4: 'Avril',
    5: 'Mai',
    6: 'Juin',
    7: 'Juillet',
    8: 'Aout',
    9: 'Septembre',
    10: 'Octobre',
    11: 'Novembre',
    12: 'Décembre',
}


def _add_months(source_date, months):
    """Ajoute/soustrait des mois sans dependance externe."""
    month_index = source_date.month - 1 + months
    year = source_date.year + (month_index // 12)
    month = (month_index % 12) + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(source_date.day, last_day)
    return date(year, month, day)


def build_fiscal_period_options(settings_instance=None, months_count=12, today=None):
    """
    Construit une liste de periodes mensuelles (mois+annee) ordonnees de la plus recente
    a la plus ancienne, alignee sur la fin de l'annee financiere de Setting.
    """
    reference_date = today or date.today()
    year_end_month = 12

    if settings_instance and settings_instance.annee_financiere:
        year_end_month = settings_instance.annee_financiere.month

    # Trouver la fin de l'exercice courant selon le mois de cloture.
    end_year = reference_date.year
    if reference_date.month > year_end_month:
        end_year += 1

    fiscal_end_date = date(end_year, year_end_month, 1)
    current_period = date(reference_date.year, reference_date.month, 1)

    # Si on est au-dela de la date courante, reculer jusqu'au mois courant.
    while fiscal_end_date > current_period:
        fiscal_end_date = _add_months(fiscal_end_date, -1)

    periods = []
    for offset in range(months_count):
        period_date = _add_months(fiscal_end_date, -offset)
        month_value = f"{period_date.month:02d}"
        year_value = str(period_date.year)
        periods.append({
            'value': f"{year_value}-{month_value}",
            'label': f"{MONTH_LABELS_FR[period_date.month]} {year_value}",
            'mois': month_value,
            'annee': year_value,
        })

    return periods


def site_settings(request):
    settings = Setting.objects.first()
    fiscal_period_options = build_fiscal_period_options(settings)

    if settings:
        request.session['nom'] = settings.nom
        request.session['logo'] = settings.logo or 'images/logos/images.png'
        request.session['phone'] = settings.phone
        request.session['adresse'] = settings.adresse
        request.session['ville'] = settings.ville
        request.session['code_postal'] = settings.code_postal
        request.session['pays'] = settings.pays
        request.session['email'] = settings.email
        request.session['compte_tps'] = settings.compte_tps_percue_id
        request.session['compte_tvq'] = settings.compte_tvq_percue_id
        request.session['compte_tps_payee'] = settings.compte_tps_payee_id
        request.session['compte_tvq_payee'] = settings.compte_tvq_payee_id
        request.session['compte_fr_retard'] = settings.compte_fr_retard_id
        request.session['compte_tps_label'] = str(settings.compte_tps_percue) if settings.compte_tps_percue else ''
        request.session['compte_tvq_label'] = str(settings.compte_tvq_percue) if settings.compte_tvq_percue else ''
        request.session['compte_fr_retard_label'] = str(settings.compte_fr_retard) if settings.compte_fr_retard else ''

    return {
        'site_settings': settings,
        'fiscal_period_options': fiscal_period_options,
    }