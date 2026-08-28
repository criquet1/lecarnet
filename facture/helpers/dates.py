from calendar import monthrange
from datetime import date, timedelta
from facture.constants import MONTH_LABELS_FR, WEEKDAY_LABELS_FR

def _date_reference_fin_exercice(annee, settings_instance):
    """Date cible (jour/mois) pour la fin d'exercice d'une année donnée,
    avant ajustement éventuel au jour de semaine le plus proche."""
    closing_month = 12
    closing_day = 31
    if settings_instance:
        if settings_instance.fin_annee_mois:
            closing_month = settings_instance.fin_annee_mois
        if settings_instance.fin_annee_jour:
            closing_day = settings_instance.fin_annee_jour

    jour_ajuste = min(closing_day, monthrange(annee, closing_month)[1])
    return date(annee, closing_month, jour_ajuste)


def _jour_semaine_le_plus_proche(reference, jour_semaine_cible):
    """Retourne la date, autour de `reference`, dont le jour de semaine
    (0=lundi ... 6=dimanche) correspond à `jour_semaine_cible` et qui est
    la plus proche de `reference` (avant ou après)."""
    ecart = (jour_semaine_cible - reference.weekday()) % 7
    if ecart > 3:
        ecart -= 7
    return reference + timedelta(days=ecart)


def date_fin_exercice_pour_annee(annee, settings_instance=None):
    """Calcule la date de fin d'exercice pour une année civile donnée,
    en tenant compte du jour de semaine optionnel (fin_annee_jour_semaine)."""
    reference = _date_reference_fin_exercice(annee, settings_instance)
    jour_semaine = getattr(settings_instance, 'fin_annee_jour_semaine', None) if settings_instance else None
    if jour_semaine is not None:
        return _jour_semaine_le_plus_proche(reference, jour_semaine)
    return reference


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

    date_fin = date_fin_exercice_pour_annee(closing_year, settings_instance)
    month_label = MONTH_LABELS_FR[date_fin.month].lower()

    jour_semaine = getattr(settings_instance, 'fin_annee_jour_semaine', None) if settings_instance else None
    if jour_semaine is not None:
        weekday_label = WEEKDAY_LABELS_FR[date_fin.weekday()].lower()
        return f"Pour l'année se terminant le {weekday_label} {date_fin.day} {month_label} {date_fin.year}"

    return f"Pour l'année au {date_fin.day} {month_label} {date_fin.year}"


def prochaine_date_fin_exercice(reference_date, settings_instance=None):
    annee = reference_date.year
    candidate = date_fin_exercice_pour_annee(annee, settings_instance)

    if candidate < reference_date:
        annee += 1
        candidate = date_fin_exercice_pour_annee(annee, settings_instance)

    return candidate


def exercice_pour_working_period(working_period):
    from compte.models import ExerciceFinancier
    from datetime import date

    reference_date = date(working_period['year'], working_period['month'], 1)
    return ExerciceFinancier.objects.filter(
        date_debut__lte=reference_date,
        date_fin__gte=reference_date,
    ).order_by('-date_debut').first()


def exercice_pour_date(date_cible):
    from compte.models import ExerciceFinancier

    return ExerciceFinancier.objects.filter(
        date_debut__lte=date_cible,
        date_fin__gte=date_cible,
    ).order_by('-date_debut').first()


def verifier_exercice_modifiable(date_cible):
    exercice = exercice_pour_date(date_cible)
    if exercice and exercice.est_audite:
        raise ValueError(
            f"L'exercice se terminant le {exercice.date_fin} est audité et verrouillé. "
            "Annule l'audit de cet exercice avant d'y ajouter ou modifier une écriture."
        )