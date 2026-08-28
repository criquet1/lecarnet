from decimal import Decimal
from django.db.models import Sum

from facture.models import (
    Client,
    CompagnieSoldeDepart,
    Fournisseur,
    Tr_desc,
    Tr_detail,
)
from facture.constants import MODE_CAP, MODE_CAR
from facture.helpers.money import money

from facture.helpers.dates import closing_date_label
from facture.services.ledger_sql import (
    fetch_compte_solde,
    fetch_compte_solde_pour_exercice,
)


def _fetch_blocks_scoped(compte_id, compagnies, is_cap, exercice, soldes_reportes_map):
    """Construit les blocs (mouvements + solde) par compagnie.

    Quand un exercice est fourni, les mouvements sont bornes a cet exercice
    et le solde de depart de chaque compagnie inclut les mouvements
    anterieurs a l'exercice - exactement comme le fait le Grand livre pour
    le solde du compte. Sans exercice, le comportement historique (cumul
    depuis le tout debut) est conserve.
    """
    tr_desc_filter = {'tr_desc__fournisseur__isnull': False} if is_cap else {'tr_desc__client__isnull': False}
    base_qs = Tr_detail.objects.filter(compte_id=compte_id, **tr_desc_filter)

    rows_by_company = {compagnie.id: [] for compagnie in compagnies}
    solde_avant_par_compagnie = {
        compagnie.id: soldes_reportes_map.get(compagnie.id, Decimal('0'))
        for compagnie in compagnies
    }

    if exercice:
        group_field = 'tr_desc__fournisseur_id' if is_cap else 'tr_desc__client_id'
        avant_totaux = base_qs.filter(
            tr_desc__date__lt=exercice.date_debut,
        ).values(group_field).annotate(total=Sum('montant'))
        for row in avant_totaux:
            compagnie_id = row[group_field]
            if compagnie_id in solde_avant_par_compagnie:
                solde_avant_par_compagnie[compagnie_id] += (row['total'] or Decimal('0'))

        details_qs = base_qs.filter(
            tr_desc__date__gte=exercice.date_debut,
            tr_desc__date__lte=exercice.date_fin,
        )
        solde_depart_label = 'Solde de départ'
    else:
        details_qs = base_qs
        solde_depart_label = 'Solde reporté'

    details = details_qs.select_related(
        'tr_desc__fournisseur',
        'tr_desc__client',
        'tr_desc__source',
    ).order_by('tr_desc__date', 'tr_desc_id', 'id')

    soldes_by_company = dict(solde_avant_par_compagnie)

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

    blocks = []
    for compagnie in compagnies:
        rows = rows_by_company.get(compagnie.id, [])
        solde_avant = solde_avant_par_compagnie.get(compagnie.id, Decimal('0'))
        if solde_avant != Decimal('0'):
            rows.insert(0, {
                'date': None,
                'source': None,
                'description': solde_depart_label,
                'debit': Decimal('0'),
                'credit': Decimal('0'),
                'solde': solde_avant,
            })
        blocks.append({
            'compagnie': compagnie,
            'rows': rows,
            'solde_reporte': solde_avant,
            'solde_final': soldes_by_company.get(compagnie.id, Decimal('0')),
        })

    total_des_soldes = sum((block['solde_final'] for block in blocks), Decimal('0'))
    return blocks, total_des_soldes


def build_compte_mode_context(mode, settings_instance, exercice=None):
    if mode not in {MODE_CAP, MODE_CAR}:
        raise ValueError("Mode invalide pour compte mode")

    is_cap = mode == MODE_CAP
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

    compte_solde_grand_livre = Decimal('0')
    blocks = []
    total_des_soldes = Decimal('0')

    if compte_id:
        blocks, total_des_soldes = _fetch_blocks_scoped(
            compte_id, compagnies, is_cap, exercice, soldes_reportes_map,
        )

        if exercice:
            compte_solde_grand_livre = fetch_compte_solde_pour_exercice(compte_id, exercice.id)
        else:
            compte_solde_grand_livre = fetch_compte_solde(compte_id)

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
