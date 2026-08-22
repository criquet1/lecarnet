from decimal import Decimal
from calendar import monthrange
from datetime import date

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from facture.helpers.money import money
from facture.models import Compagnie, RapportTaxes, Tr_desc, Tr_detail, Source
from facture.utils import (
    ensure_tax_authority_companies,
    tax_target_mode_from_setting,
    TAX_AUTHORITY_COMPANY_TPS,
    TAX_AUTHORITY_COMPANY_TVQ,
)


def remove_line_from_report(report, detail_id):
    """Retire une ligne du rapport de taxes. Retourne le nombre de lignes retirees."""
    return Tr_detail.objects.filter(pk=detail_id, rapport_taxes=report).update(rapport_taxes=None)


def transmit_report(report, settings_instance, next_no_ej):
    """Transmet le rapport de taxes : cree les ecritures de report vers l'entite fiscale
    (Fournisseur si mode CAP, Client si mode CAR). Retourne (posted_count, mode_label).
    Leve ValueError si la configuration est incomplete."""
    tax_mode = tax_target_mode_from_setting(settings_instance)
    is_cap = tax_mode == Compagnie.MODE_CAP
    mode_label = "CAP" if is_cap else "CAR"
    target_compte = settings_instance.cap if is_cap else settings_instance.car

    if not target_compte:
        raise ValueError(
            f"Compte {mode_label} non configure dans Setting. Configure ce compte avant de transmettre le rapport."
        )

    tax_account_map = {
        'TPS': {'percue': settings_instance.compte_tps_percue, 'payee': settings_instance.compte_tps_payee},
        'TVQ': {'percue': settings_instance.compte_tvq_percue, 'payee': settings_instance.compte_tvq_payee},
    }
    missing_accounts = [
        f"{tax_name} {line_name}"
        for tax_name, accounts in tax_account_map.items()
        for line_name, account in accounts.items()
        if account is None
    ]
    if missing_accounts:
        raise ValueError(
            "Comptes taxes manquants dans Setting pour la transmission: " + ", ".join(missing_accounts) + "."
        )

    tax_entities = ensure_tax_authority_companies(settings_instance)
    entities_by_tax = {
        'TPS': tax_entities.get(TAX_AUTHORITY_COMPANY_TPS),
        'TVQ': tax_entities.get(TAX_AUTHORITY_COMPANY_TVQ),
    }
    if not entities_by_tax['TPS'] or not entities_by_tax['TVQ']:
        raise ValueError("Impossible de preparer les entites fiscales TPS/TVQ.")

    report_date = date(report.annee, report.mois, monthrange(report.annee, report.mois)[1])
    source_rapport, _ = Source.objects.get_or_create(nom='Rapport de taxes')
    posted_count = 0

    with transaction.atomic():
        for tax_name in ('TPS', 'TVQ'):
            percue_compte = tax_account_map[tax_name]['percue']
            payee_compte = tax_account_map[tax_name]['payee']
            entite_fiscale = entities_by_tax[tax_name]

            # Solde reel a la fin du mois: on poste l'inverse pour ramener
            # chaque compte de taxe a zero apres transmission.
            percue_balance = Tr_detail.objects.filter(
                compte=percue_compte,
                tr_desc__date__lte=report_date,
            ).aggregate(total=Sum('montant')).get('total') or Decimal('0')
            payee_balance = Tr_detail.objects.filter(
                compte=payee_compte,
                tr_desc__date__lte=report_date,
            ).aggregate(total=Sum('montant')).get('total') or Decimal('0')

            percue_amount = money(-percue_balance)
            payee_amount = money(-payee_balance)
            target_line_amount = money(-(percue_amount + payee_amount))

            if percue_amount == Decimal('0') and payee_amount == Decimal('0') and target_line_amount == Decimal('0'):
                continue

            tr_desc_kwargs = {
                'no_ej': next_no_ej(),
                'date': report_date,
                'desc_ctb': f"Rapport de taxes {tax_name} {report.annee}-{report.mois:02d}",
                'source': source_rapport,
            }
            if is_cap:
                tr_desc_kwargs['fournisseur'] = entite_fiscale
            else:
                tr_desc_kwargs['client'] = entite_fiscale

            tr_desc = Tr_desc.objects.create(**tr_desc_kwargs)

            if percue_amount != Decimal('0'):
                Tr_detail.objects.create(tr_desc=tr_desc, compte=percue_compte, montant=percue_amount)
            if payee_amount != Decimal('0'):
                Tr_detail.objects.create(tr_desc=tr_desc, compte=payee_compte, montant=payee_amount)
            if target_line_amount != Decimal('0'):
                Tr_detail.objects.create(tr_desc=tr_desc, compte=target_compte, montant=target_line_amount)
            posted_count += 1

        report.transmis_le = timezone.now()
        report.save(update_fields=['transmis_le'])

    return posted_count, mode_label


def undo_transmit_report(report):
    """Annule la transmission : supprime les ecritures de report crees pour ce mois.
    Retourne le nombre d'ecritures supprimees."""
    report_date = date(report.annee, report.mois, monthrange(report.annee, report.mois)[1])
    expected_descriptions = [
        f"Rapport de taxes TPS {report.annee}-{report.mois:02d}",
        f"Rapport de taxes TVQ {report.annee}-{report.mois:02d}",
    ]

    with transaction.atomic():
        source_rapport = Source.objects.filter(nom='Rapport de taxes').first()
        transmission_entries = Tr_desc.objects.none()
        if source_rapport:
            transmission_entries = Tr_desc.objects.filter(
                source=source_rapport,
                date=report_date,
                desc_ctb__in=expected_descriptions,
            ).filter(
                Q(fournisseur__nom__in=[TAX_AUTHORITY_COMPANY_TPS, TAX_AUTHORITY_COMPANY_TVQ]) |
                Q(client__nom__in=[TAX_AUTHORITY_COMPANY_TPS, TAX_AUTHORITY_COMPANY_TVQ])
            )

        deleted_entries = transmission_entries.count()
        if deleted_entries:
            transmission_entries.delete()

        RapportTaxes.objects.filter(pk=report.pk).update(transmis_le=None)

    return deleted_entries