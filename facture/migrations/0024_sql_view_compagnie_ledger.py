from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0023_tr_desc_facture_trdesc_date_id_idx'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                DROP VIEW IF EXISTS facture_v_compagnie_ledger_lignes;
                CREATE VIEW facture_v_compagnie_ledger_lignes AS
                SELECT
                    td.id AS tr_detail_id,
                    td.tr_desc_id AS tr_desc_id,
                    td.compte_id AS compte_id,
                    comp.id AS compagnie_id,
                    comp.nom AS compagnie_nom,
                    comp.cap_ou_car AS cap_ou_car,
                    t.date AS tr_date,
                    t.description AS tr_description,
                    src.nom AS source_nom,
                    CASE WHEN td.montant >= 0 THEN td.montant ELSE 0 END AS debit,
                    CASE WHEN td.montant < 0 THEN ABS(td.montant) ELSE 0 END AS credit,
                    SUM(td.montant) OVER (
                        PARTITION BY comp.id, td.compte_id
                        ORDER BY t.date, td.tr_desc_id, td.id
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ) AS solde_compagnie
                FROM facture_tr_detail td
                INNER JOIN facture_tr_desc t ON t.id = td.tr_desc_id
                INNER JOIN facture_compagnie comp ON comp.id = t.compagnie_id
                LEFT JOIN facture_source src ON src.id = t.source_id
                WHERE comp.cap_ou_car IN ('CAP', 'CAR');
            """,
            reverse_sql="DROP VIEW IF EXISTS facture_v_compagnie_ledger_lignes;",
        ),
    ]
