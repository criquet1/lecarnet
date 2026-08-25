from types import SimpleNamespace

from django.db import connection, connections
from decimal import Decimal

from facture.models import SoldeFin, Tr_detail, RapportTaxes
from facture.helpers.decimal_utils import coerce_decimal


def fetch_compte_solde(compte_id):
    if not compte_id:
        return Decimal('0')

    return coerce_decimal(
        SoldeFin.objects.filter(compte_numero_id=compte_id)
        .values_list('solde_final', flat=True)
        .first()
    )


def fetch_compte_mode_blocks_from_sql_view(mode, compte_id, compagnies):
    db_alias = ledger_db_alias()
    query = """
        SELECT
            compagnie_id,
            compagnie_nom,
            tr_date,
            source_nom,
            tr_description,
            debit,
            credit,
            solde_compagnie
        FROM facture_v_compagnie_ledger_lignes
        WHERE cap_ou_car = %s AND compte_id = %s
        ORDER BY compagnie_nom, tr_date, tr_desc_id, tr_detail_id
    """

    rows_by_company = {compagnie.id: [] for compagnie in compagnies}
    soldes_by_company = {compagnie.id: Decimal('0') for compagnie in compagnies}

    with connections[db_alias].cursor() as cursor:
        cursor.execute(query, [mode, compte_id])
        for (
            compagnie_id,
            _compagnie_nom,
            tr_date,
            source_nom,
            tr_description,
            debit,
            credit,
            solde_compagnie,
        ) in cursor.fetchall():
            if compagnie_id not in rows_by_company:
                continue

            rows_by_company[compagnie_id].append({
                'date': tr_date,
                'source': SimpleNamespace(nom=source_nom) if source_nom else None,
                'description': tr_description,
                'debit': coerce_decimal(debit),
                'credit': coerce_decimal(credit),
                'solde': coerce_decimal(solde_compagnie),
            })
            soldes_by_company[compagnie_id] = coerce_decimal(solde_compagnie)

    blocks = []
    total_des_soldes = Decimal('0')
    for compagnie in compagnies:
        company_solde = soldes_by_company.get(compagnie.id, Decimal('0'))
        total_des_soldes += company_solde
        blocks.append({
            'compagnie': compagnie,
            'rows': rows_by_company.get(compagnie.id, []),
            'solde_final': company_solde,
        })

    return blocks, total_des_soldes


def ledger_db_alias():
    return Tr_detail.objects.all().db




def fetch_or_create_monthly_tax_report(selected_year, selected_month, tax_account_ids):
    # 1. Base query
    base_tax_details = Tr_detail.objects.select_related(
        'tr_desc__client',
        'tr_desc__fournisseur',
        'compte',
        'rapport_taxes',
    ).filter(compte_id__in=tax_account_ids).order_by('tr_desc__date', 'id')

    # 2. Filter by month
    month_tax_details = base_tax_details.filter(
        tr_desc__date__year=selected_year,
        tr_desc__date__month=selected_month,
    )

    # 3. Fetch or create report
    selected_report = RapportTaxes.objects.filter(
        annee=selected_year,
        mois=selected_month,
    ).first()

    if tax_account_ids and month_tax_details.exists():
        if not selected_report:
            selected_report = RapportTaxes.objects.create(
                annee=selected_year,
                mois=selected_month,
            )

        # 4. Attach details to report if not transmitted
        if not selected_report.est_transmis:
            month_tax_details.filter(rapport_taxes__isnull=True).update(
                rapport_taxes=selected_report
            )

    return selected_report, month_tax_details
