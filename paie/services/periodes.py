"""
Service de génération des PeriodePaie pour une année donnée.

La règle métier : la date de paiement est le prochain jour de semaine
configuré dans les paramètres (Setting.date_premier_paiement_paie_annee)
après la fin de période; si ce jour tombe une fin de semaine ou un jour
férié QC/CA, on recule au dernier jour ouvrable.
"""
from calendar import monthrange
from datetime import date, timedelta

from django.utils.connection import ConnectionDoesNotExist
from django.db.utils import OperationalError, ProgrammingError
from holidays import country_holidays


DEFAULT_PAYDAY_WEEKDAY = 3  # Jeudi

def payday_weekday_from_anchor(date_premier_paiement_paie_annee):
    if date_premier_paiement_paie_annee:
        return date_premier_paiement_paie_annee.weekday()
    return DEFAULT_PAYDAY_WEEKDAY


def _to_previous_business_day(candidate: date) -> date:
    while True:
        feries = country_holidays('CA', subdiv='QC', years=[candidate.year])
        if candidate.weekday() < 5 and candidate not in feries:
            return candidate
        candidate -= timedelta(days=1)


def next_payday_after(date_fin: date, payday_weekday: int) -> date:
    """Retourne la prochaine date de paiement strictement après date_fin."""
    days_ahead = (payday_weekday - date_fin.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    candidate = date_fin + timedelta(days=days_ahead)
    return _to_previous_business_day(candidate)


def _add_months(d: date, n: int) -> date:
    month_idx = d.month - 1 + n
    year = d.year + month_idx // 12
    month = (month_idx % 12) + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


# ---------------------------------------------------------------------------
# Génération des triplets (date_debut, date_fin, date_paie)
# ---------------------------------------------------------------------------

def _periods_for_year(code: str, date_debut_anchor: date, annee: int, payday_weekday: int):
    """
    Retourne tous les triplets (date_debut, date_fin, date_paie)
    dont date_paie.year == annee.

    L'ancre peut être n'importe quelle année passée ou future; la fonction
    avance/recule jusqu'à trouver les périodes cibles.
    """
    from paie.models import FrequencePaie

    periods = []

    if code in (FrequencePaie.HEBDOMADAIRE, FrequencePaie.AUX_2_SEMAINES):
        step = 7 if code == FrequencePaie.HEBDOMADAIRE else 14
        # Point de départ: un peu avant le 1er janvier de l'année cible
        # pour attraper les périodes à cheval sur déc/jan.
        earliest = date(annee, 1, 1) - timedelta(days=step + 7)
        if date_debut_anchor <= earliest:
            diff = (earliest - date_debut_anchor).days
            start_idx = diff // step
        else:
            start_idx = 0
        # Itérer jusqu'à max 100 périodes au-delà du début ciblé
        for idx in range(start_idx, start_idx + (56 if step == 7 else 29)):
            date_debut = date_debut_anchor + timedelta(days=idx * step)
            date_fin = date_debut + timedelta(days=step - 1)
            date_paie = next_payday_after(date_fin, payday_weekday)
            if date_paie.year < annee:
                continue
            if date_paie.year > annee:
                break
            periods.append((date_debut, date_fin, date_paie))

    elif code == FrequencePaie.PAR_MOIS:
        current_start = date_debut_anchor
        # Avance rapidement si l'ancre est très ancienne
        while current_start.year < annee - 1:
            current_start = _add_months(current_start, 1)
        for _ in range(36):
            next_start = _add_months(current_start, 1)
            date_fin = next_start - timedelta(days=1)
            date_paie = next_payday_after(date_fin, payday_weekday)
            if date_paie.year < annee:
                current_start = next_start
                continue
            if date_paie.year > annee:
                break
            periods.append((current_start, date_fin, date_paie))
            current_start = next_start

    elif code == FrequencePaie.DEUX_FOIS_MOIS:
        current_start = date_debut_anchor
        # Avance rapidement si l'ancre est très ancienne
        while current_start.year < annee - 1:
            if current_start.day <= 15:
                current_start = current_start.replace(day=16)
            else:
                current_start = _add_months(current_start.replace(day=1), 1)
        for _ in range(60):
            if current_start.day <= 15:
                date_fin = current_start.replace(day=15)
            else:
                last_day = monthrange(current_start.year, current_start.month)[1]
                date_fin = current_start.replace(day=last_day)
            date_paie = next_payday_after(date_fin, payday_weekday)
            if date_paie.year < annee:
                current_start = date_fin + timedelta(days=1)
                continue
            if date_paie.year > annee:
                break
            periods.append((current_start, date_fin, date_paie))
            current_start = date_fin + timedelta(days=1)

    return periods


# ---------------------------------------------------------------------------
# Point d'entrée public
# ---------------------------------------------------------------------------

def generate_periodes_annee(annee: int, db_alias: str) -> int:
    """
    Pré-remplit les PeriodePaie pour l'année ``annee`` dans la base ``db_alias``.

    Ne touche pas aux périodes existantes (get_or_create).
    Retourne le nombre de périodes nouvellement créées.
    """
    from compte.models import Setting
    from paie.models import PeriodePaie

    try:
        setting = (
            Setting.objects
            .using(db_alias)
            .select_related('frequence_paie')
            .first()
        )
    except (ConnectionDoesNotExist, OperationalError, ProgrammingError):
        return 0

    if not setting or not setting.frequence_paie_id:
        return 0

    frequence = setting.frequence_paie
    date_debut_anchor = setting.date_debut_periode_paie_annee
    if not date_debut_anchor:
        return 0

    payday_weekday = payday_weekday_from_anchor(setting.date_premier_paiement_paie_annee)
    triplets = _periods_for_year(frequence.code, date_debut_anchor, annee, payday_weekday)
    created_count = 0

    for date_debut, date_fin, date_paie in triplets:
        periode, created = PeriodePaie.objects.using(db_alias).get_or_create(
            frequence_paie=frequence,
            date_debut=date_debut,
            date_fin=date_fin,
            defaults={'date_paie': date_paie},
        )
        if created:
            created_count += 1
        elif (
            periode.date_paie != date_paie
            and not periode.fermee
            and not periode.paies.exists()
        ):
            periode.date_paie = date_paie
            periode.save(update_fields=['date_paie'], using=db_alias)

    return created_count
