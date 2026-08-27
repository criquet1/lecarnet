from calendar import monthrange
from facture.constants import MONTH_LABELS_FR
from datetime import date

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


def prochaine_date_fin_exercice(reference_date, settings_instance=None):
    closing_month = 12
    closing_day = 31
    if settings_instance:
        if settings_instance.fin_annee_mois:
            closing_month = settings_instance.fin_annee_mois
        if settings_instance.fin_annee_jour:
            closing_day = settings_instance.fin_annee_jour

    annee = reference_date.year
    jour_ajuste = min(closing_day, monthrange(annee, closing_month)[1])
    candidate = date(annee, closing_month, jour_ajuste)

    if candidate < reference_date:
        annee += 1
        jour_ajuste = min(closing_day, monthrange(annee, closing_month)[1])
        candidate = date(annee, closing_month, jour_ajuste)

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