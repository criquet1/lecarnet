from decimal import Decimal
from django.db import DatabaseError
from django.db.models import Sum
from types import SimpleNamespace

from facture.models import (
    Client,
    Compagnie,
    CompagnieSoldeDepart,
    Fournisseur,
    Tr_desc,
    Tr_detail,
)
from facture.helpers.money import money

from facture.helpers.dates import closing_date_label
from facture.services.ledger_sql import (
    fetch_compte_solde,
    fetch_compte_mode_blocks_from_sql_view,
    ledger_db_alias,
)



def build_compte_mode_context(mode, settings_instance):
    if mode not in {Compagnie.MODE_CAP, Compagnie.MODE_CAR}:
        raise ValueError("Mode invalide pour compte mode")

    is_cap = mode == Compagnie.MODE_CAP
    compte_id = settings_instance.cap_id if (settings_instance and is_cap) else settings_instance.car_id if settings_instance else None
    entity_model = Fournisseur if is_cap else Client
    compagnies = entity_model.objects.order_by('nom')

    if is_cap:
        report_date = Tr_desc.objects.filter(
            fournisseur__isnull=False,
        ).order_by('-date').values_list('date', flat=True).first()
    else:
        report_date = Tr_desc.objects.filter(
            client__isnull=False,
        ).order_by('-date').values_list('date', flat=True).first()
    report_year_label = closing_date_label(report_date, settings_instance)

    compte_solde_grand_livre = Decimal('0')
    blocks = []
    total_des_soldes = Decimal('0')
    if is_cap:
        soldes_reportes_map = {
            row.fournisseur_id: (row.montant or Decimal('0'))
            for row in CompagnieSoldeDepart.objects.filter(fournisseur__isnull=False)
        }
    else:
        soldes_reportes_map = {
            row.client_id: (row.montant or Decimal('0'))
            for row in CompagnieSoldeDepart.objects.filter(client__isnull=False)
        }

    if compte_id:
        try:
            compte_solde_grand_livre = fetch_compte_solde(compte_id)
            blocks, total_des_soldes = fetch_compte_mode_blocks_from_sql_view(
                mode,
                compte_id,
                compagnies,
            )
        except DatabaseError:
            compte_solde_grand_livre = Tr_detail.objects.filter(
                compte_id=compte_id,
            ).aggregate(total=Sum('montant')).get('total') or Decimal('0')

            tr_desc_filter = {'tr_desc__fournisseur__isnull': False} if is_cap else {'tr_desc__client__isnull': False}
            details = Tr_detail.objects.select_related(
                'tr_desc__fournisseur',
                'tr_desc__client',
                'tr_desc__source',
            ).filter(
                compte_id=compte_id,
                **tr_desc_filter
            ).order_by(
                'tr_desc__date',
                'tr_desc_id',
                'id',
            )

            rows_by_company = {compagnie.id: [] for compagnie in compagnies}
            soldes_by_company = {compagnie.id: Decimal('0') for compagnie in compagnies}

            for detail in details:
                compagnie_id = detail.tr_desc.fournisseur_id if is_cap else detail.tr_desc.client_id
                if compagnie_id not in rows_by_company:
                    continue

                montant = detail.montant or Decimal('0')
                debit = montant if montant >= 0 else Decimal('0')
                credit = abs(montant) if montant < 0 else Decimal('0')
                releve_source = detail.tr_desc.releves_sources.all().first()
                description = releve_source.desc_ctb if releve_source and releve_source.desc_ctb else detail.tr_desc.desc_ctb

                soldes_by_company[compagnie_id] += montant

                rows_by_company[compagnie_id].append({
                    'date': detail.tr_desc.date,
                    'source': detail.tr_desc.source,
                    'description': description,
                    'debit': debit,
                    'credit': credit,
                    'solde': soldes_by_company[compagnie_id],
                })

            for compagnie in compagnies:
                company_solde = soldes_by_company.get(compagnie.id, Decimal('0'))
                total_des_soldes += company_solde
                blocks.append({
                    'compagnie': compagnie,
                    'rows': rows_by_company.get(compagnie.id, []),
                    'solde_final': company_solde,
                })

    # Les soldes reportes (repartition CAP/CAR) doivent apparaitre dans les soldes par compagnie.
    for block in blocks:
        compagnie_id = block['compagnie'].id
        solde_reporte = soldes_reportes_map.get(compagnie_id, Decimal('0'))
        rows = block.get('rows', [])

        if solde_reporte != Decimal('0'):
            for row in rows:
                row['solde'] = (row.get('solde') or Decimal('0')) + solde_reporte

            rows.insert(0, {
                'date': None,
                'source': None,
                'description': 'Solde reporté',
                'debit': Decimal('0'),
                'credit': Decimal('0'),
                'solde': solde_reporte,
            })

        block['solde_reporte'] = solde_reporte
        block['solde_final'] = (block.get('solde_final') or Decimal('0')) + solde_reporte

    total_des_soldes = sum((block.get('solde_final') or Decimal('0') for block in blocks), Decimal('0'))

    total_des_soldes = money(total_des_soldes)
    compte_solde_grand_livre = money(compte_solde_grand_livre)
    ecart_solde = money(total_des_soldes - compte_solde_grand_livre)
    mode_code = 'CAP' if is_cap else 'CAR'

    return {
        'blocks': blocks,
        'total_des_soldes': total_des_soldes,
        'compte_solde_grand_livre': compte_solde_grand_livre,
        'ecart_solde': ecart_solde,
        'is_solde_coherent': ecart_solde == Decimal('0.00'),
        'mode_compte': settings_instance.cap if (settings_instance and is_cap) else settings_instance.car if settings_instance else None,
        'mode_code': mode_code,
        'report_year_label': report_year_label,
    }
