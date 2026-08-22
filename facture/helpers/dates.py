from calendar import monthrange
from facture.constants import MONTH_LABELS_FR

def closing_date_label(reference_date, settings_instance=None):
    if not reference_date:
        return None

    closing_month = 12
    closing_day = 31
    if settings_instance:
        if settings_instance.fin_annee_mois:
            closing_month = settings_instance.fin_annee_mois
        if settings_instance.fin_annee_jour:
            closing_day = settings_instance.fin_annee_jour

    closing_year = reference_date.year
    if (reference_date.month, reference_date.day) > (closing_month, closing_day):
        closing_year += 1

    closing_day = min(closing_day, monthrange(closing_year, closing_month)[1])
    month_label = MONTH_LABELS_FR[closing_month].lower()
    return f"Pour l'année au {closing_day} {month_label} {closing_year}"
