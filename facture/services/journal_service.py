from decimal import Decimal
import re
from types import SimpleNamespace
from facture.models import TransactionListe, Client, Compte, Fournisseur
from facture.utils import get_setting
from facture.helpers.dates import closing_date_label




def build_journal_context(exercice=None):
    entries_by_no_ej = {}
    total_debit = Decimal('0')
    total_credit = Decimal('0')

    transactions_qs = TransactionListe.objects.all()
    if exercice:
        transactions_qs = transactions_qs.filter(
            date__gte=exercice.date_debut,
            date__lte=exercice.date_fin,
        )

    # Regrouper les lignes par numéro EJ
    for row in transactions_qs:
        entry_key = (row.no_ej, row.date)
        entry = entries_by_no_ej.setdefault(entry_key, SimpleNamespace(
            no_ej=row.no_ej,
            date=row.date,
            description=row.description,
            compagnie=row.compagnie,
            source=row.source,
            details=[],
        ))
        entry.details.append(row)
        total_debit += row.debit or Decimal('0')
        total_credit += row.credit or Decimal('0')

    # Trier les détails dans chaque écriture
    journal_entries = list(entries_by_no_ej.values())
    for entry in journal_entries:
        entry.details.sort(key=lambda detail: (
            detail.credit > 0,
            detail.compte_numero,
            detail.transaction_id,
        ))

    # Trier les écritures par numéro EJ
    def no_ej_sort_value(entry):
        match = re.match(r'^EJ(\d+)$', entry.no_ej or '')
        if match:
            return int(match.group(1))
        return -1

    journal_entries.sort(key=no_ej_sort_value, reverse=True)

    # Label de fin d'année
    settings_instance = get_setting()
    report_date = exercice.date_fin if exercice else max((entry.date for entry in journal_entries if entry.date), default=None)
    report_year_label = closing_date_label(report_date, settings_instance)

    # Données pour le formulaire
    compagnies = sorted(
        [{'type': 'client', 'obj': c, 'key': f'client:{c.pk}'} for c in Client.objects.filter(active=True)] +
        [{'type': 'fournisseur', 'obj': f, 'key': f'fournisseur:{f.pk}'} for f in Fournisseur.objects.filter(active=True)],
        key=lambda item: item['obj'].nom.lower()
    )
    comptes = Compte.objects.order_by('numero')

    return {
        'title': "Journal général",
        'journal_entries': journal_entries,
        'total_debit': total_debit,
        'total_credit': total_credit,
        'report_year_label': report_year_label,
        'compagnies': compagnies,
        'comptes': comptes,
        'compte_cap_id': settings_instance.cap_id if settings_instance else None,
        'compte_car_id': settings_instance.car_id if settings_instance else None,
    }
