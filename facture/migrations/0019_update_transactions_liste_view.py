from django.db import migrations

SQL_UPDATE_VIEW = """
CREATE OR REPLACE VIEW transactions_liste AS
SELECT
    t.id AS transaction_id,
    d.no_ej AS no_ej,
    d.date AS date,
    COALESCE(cl.nom, fo.nom, c.nom) AS compagnie,
    d.desc_ctb AS description,
    s.nom AS source,
    cc.numero AS compte_numero,
    cc.libelle AS compte_libelle,
    t.rapport_taxes_id AS rapport_taxes_id,
    CASE WHEN t.montant < 0 THEN ABS(t.montant) ELSE 0 END AS debit,
    CASE WHEN t.montant > 0 THEN t.montant ELSE 0 END AS credit
FROM facture_tr_detail t
JOIN facture_tr_desc d ON d.id = t.tr_desc_id
LEFT JOIN facture_compagnie c ON c.id = d.compagnie_id
LEFT JOIN facture_client cl ON cl.id = d.client_id
LEFT JOIN facture_fournisseur fo ON fo.id = d.fournisseur_id
LEFT JOIN facture_source s ON s.id = d.source_id
JOIN compte_compte cc ON cc.numero = t.compte_id
ORDER BY d.date, d.no_ej, t.id;
"""

SQL_REVERT_VIEW = """
CREATE OR REPLACE VIEW transactions_liste AS
SELECT
    t.id AS transaction_id,
    d.no_ej AS no_ej,
    d.date AS date,
    c.nom AS compagnie,
    d.desc_ctb AS description,
    s.nom AS source,
    cc.numero AS compte_numero,
    cc.libelle AS compte_libelle,
    t.rapport_taxes_id AS rapport_taxes_id,
    CASE WHEN t.montant < 0 THEN ABS(t.montant) ELSE 0 END AS debit,
    CASE WHEN t.montant > 0 THEN t.montant ELSE 0 END AS credit
FROM facture_tr_detail t
JOIN facture_tr_desc d ON d.id = t.tr_desc_id
LEFT JOIN facture_compagnie c ON c.id = d.compagnie_id
LEFT JOIN facture_source s ON s.id = d.source_id
JOIN compte_compte cc ON cc.numero = t.compte_id
ORDER BY d.date, d.no_ej, t.id;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0018_alter_compagniesoldedepart_options_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql=SQL_UPDATE_VIEW,
            reverse_sql=SQL_REVERT_VIEW,
        ),
    ]