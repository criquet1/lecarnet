from facture.services.taxes_service import (
    build_tax_blocks,
    calculer_montants_mensuels,
    calculer_periode_mensuelle,
    construire_formulaire_taxes,
    construire_lignes_taxes,
)


def enrich_report_with_calculations(report, tps_percue_id, tps_payee_id, tvq_percue_id, tvq_payee_id):
    # 1. Tax blocks
    report.tax_blocks = build_tax_blocks(
        report.details_taxes.all(),
        tps_percue_id,
        tps_payee_id,
        tvq_percue_id,
        tvq_payee_id,
    )

    # 2. Formulaire
    report.formulaire = construire_formulaire_taxes(
        report.tax_blocks['TPS'],
        report.tax_blocks['TVQ'],
    )

    # 3. Lignes fusionnées
    report.merged_rows = construire_lignes_taxes(
        report.details_taxes.all(),
        tps_percue_id,
        tps_payee_id,
        tvq_percue_id,
        tvq_payee_id,
    )

    # 4. Montants mensuels
    montants = calculer_montants_mensuels(
        report.tax_blocks['TPS'],
        report.tax_blocks['TVQ'],
    )
    report.montant_101 = montants['montant_101']
    report.solde_300 = montants['solde_300']

    # 5. Période mensuelle
    report.periode_debut, report.periode_fin = calculer_periode_mensuelle(
        report.annee,
        report.mois,
    )

    return report
