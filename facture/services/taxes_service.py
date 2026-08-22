from facture.helpers.money import money
from decimal import Decimal
from datetime import date
from calendar import monthrange


def calculer_periode_mensuelle(annee, mois):
    debut = date(annee, mois, 1)
    fin = date(annee, mois, monthrange(annee, mois)[1])
    return debut, fin


def calculer_montants_mensuels(tps_block, tvq_block):
    total_taxes_percues = tps_block['total_percue'] + tvq_block['total_percue']
    montant_101 = money(total_taxes_percues / Decimal('0.14975'))
    solde_300 = money(tps_block['solde_a_reclamer'] + tvq_block['solde_a_reclamer'])

    return {
        'montant_101': montant_101,
        'solde_300': solde_300,
    }


def construire_lignes_taxes(details, tps_percue_id, tps_payee_id, tvq_percue_id, tvq_payee_id):
    rows_by_desc = {}

    for detail in details:
        tax_type = None
        is_percue = False
        amount = money(detail.montant)

        if detail.compte_id == tps_percue_id:
            tax_type = 'TPS'
            is_percue = True
        elif detail.compte_id == tps_payee_id:
            tax_type = 'TPS'
            is_percue = False
        elif detail.compte_id == tvq_percue_id:
            tax_type = 'TVQ'
            is_percue = True
        elif detail.compte_id == tvq_payee_id:
            tax_type = 'TVQ'
            is_percue = False

        if not tax_type:
            continue

        desc_id = detail.tr_desc_id
        if desc_id not in rows_by_desc:
            rows_by_desc[desc_id] = {
                'date': detail.tr_desc.date,
                'compagnie_nom': detail.tr_desc.client.nom if detail.tr_desc.client_id else (detail.tr_desc.fournisseur.nom if detail.tr_desc.fournisseur_id else '-'),
                'facture': detail.tr_desc.desc_ctb or '-',
                'tps_percue': None,
                'tps_payee': None,
                'tvq_percue': None,
                'tvq_payee': None,
            }

        row = rows_by_desc[desc_id]
        value = money(abs(amount)) if is_percue else amount
        key = f"{tax_type.lower()}_{'percue' if is_percue else 'payee'}"
        row[key] = value

    return sorted(rows_by_desc.values(), key=lambda r: r['date'])


def construire_formulaire_taxes(tps_block, tvq_block):
    total_taxes_percues = tps_block['total_percue'] + tvq_block['total_percue']

    montant_101 = money(total_taxes_percues / Decimal('0.14975'))
    solde_300 = money(tps_block['solde_a_reclamer'] + tvq_block['solde_a_reclamer'])

    return {
        '101': montant_101,
        '105': tps_block['total_percue'],
        '108': tps_block['total_payee'],
        '113': tps_block['solde_a_reclamer'],
        '205': tvq_block['total_percue'],
        '208': tvq_block['total_payee'],
        '213': tvq_block['solde_a_reclamer'],
        '300': solde_300,
    }


from facture.helpers.money import money
from decimal import Decimal

def build_tax_blocks(details, tps_percue_id, tps_payee_id, tvq_percue_id, tvq_payee_id):
    blocks = {
        'TPS': {
            'rows': [],
            'total_percue': Decimal('0'),
            'total_percue_signee': Decimal('0'),
            'total_payee': Decimal('0'),
            'solde_a_reclamer': Decimal('0'),
        },
        'TVQ': {
            'rows': [],
            'total_percue': Decimal('0'),
            'total_percue_signee': Decimal('0'),
            'total_payee': Decimal('0'),
            'solde_a_reclamer': Decimal('0'),
        },
    }

    for detail in details:
        tax_type = None
        percue = None
        percue_signee = None
        payee = None
        amount = money(detail.montant)

        if detail.compte_id == tps_percue_id:
            tax_type = 'TPS'
            percue_signee = amount
            percue = money(abs(amount))
        elif detail.compte_id == tps_payee_id:
            tax_type = 'TPS'
            payee = amount
        elif detail.compte_id == tvq_percue_id:
            tax_type = 'TVQ'
            percue_signee = amount
            percue = money(abs(amount))
        elif detail.compte_id == tvq_payee_id:
            tax_type = 'TVQ'
            payee = amount

        if not tax_type:
            continue

        if percue is not None:
            blocks[tax_type]['total_percue'] = money(blocks[tax_type]['total_percue'] + percue)
        if percue_signee is not None:
            blocks[tax_type]['total_percue_signee'] = money(blocks[tax_type]['total_percue_signee'] + percue_signee)
        if payee is not None:
            blocks[tax_type]['total_payee'] = money(blocks[tax_type]['total_payee'] + payee)

        blocks[tax_type]['rows'].append({
            'id': detail.id,
            'date': detail.tr_desc.date,
            'compagnie_nom': detail.tr_desc.client.nom if detail.tr_desc.client_id else (detail.tr_desc.fournisseur.nom if detail.tr_desc.fournisseur_id else '-'),
            'facture': detail.tr_desc.desc_ctb or '-',
            'percue': percue,
            'payee': payee,
        })

    for tax_type in ('TPS', 'TVQ'):
        blocks[tax_type]['solde_a_reclamer'] = money(
            blocks[tax_type]['total_percue_signee'] + blocks[tax_type]['total_payee']
        )

    return blocks
