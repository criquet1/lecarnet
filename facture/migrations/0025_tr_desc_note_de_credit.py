from django.db import migrations, models


DROP_VIEWS_SQL = """
DROP VIEW IF EXISTS facture_v_compagnie_ledger_lignes;
DROP VIEW IF EXISTS facture_v_grand_livre_lignes;
DROP VIEW IF EXISTS facture_v_balance_verification;
"""


CREATE_VIEWS_SQL = """
CREATE VIEW facture_v_grand_livre_lignes AS
SELECT
    td.id AS tr_detail_id,
    td.tr_desc_id AS tr_desc_id,
    td.compte_id AS compte_id,
    c.numero AS compte_numero,
    c.libelle AS compte_libelle,
    t.date AS tr_date,
    t.no_ej AS no_ej,
    comp.nom AS compagnie_nom,
    t.description AS tr_description,
    src.nom AS source_nom,
    CASE WHEN td.montant >= 0 THEN td.montant ELSE 0 END AS debit,
    CASE WHEN td.montant < 0 THEN ABS(td.montant) ELSE 0 END AS credit,
    SUM(td.montant) OVER (
        PARTITION BY td.compte_id
        ORDER BY t.date, td.tr_desc_id, td.id
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS solde
FROM facture_tr_detail td
INNER JOIN facture_tr_desc t ON t.id = td.tr_desc_id
INNER JOIN compte_compte c ON c.numero = td.compte_id
LEFT JOIN facture_compagnie comp ON comp.id = t.compagnie_id
LEFT JOIN facture_source src ON src.id = t.source_id;

CREATE VIEW facture_v_balance_verification AS
WITH mouvements AS (
    SELECT
        td.compte_id AS compte_id,
        SUM(td.montant) AS total_mouvements
    FROM facture_tr_detail td
    GROUP BY td.compte_id
)
SELECT
    c.numero AS compte_id,
    c.numero AS compte_numero,
    c.libelle AS compte_libelle,
    COALESCE(s.solde_depart, 0) AS solde_depart,
    COALESCE(m.total_mouvements, 0) AS total_mouvements,
    COALESCE(s.solde_depart, 0) + COALESCE(m.total_mouvements, 0) AS solde,
    CASE
        WHEN (COALESCE(s.solde_depart, 0) + COALESCE(m.total_mouvements, 0)) >= 0
            THEN (COALESCE(s.solde_depart, 0) + COALESCE(m.total_mouvements, 0))
        ELSE 0
    END AS debit,
    CASE
        WHEN (COALESCE(s.solde_depart, 0) + COALESCE(m.total_mouvements, 0)) < 0
            THEN ABS(COALESCE(s.solde_depart, 0) + COALESCE(m.total_mouvements, 0))
        ELSE 0
    END AS credit
FROM compte_compte c
LEFT JOIN compte_soldeauxlivres s ON s.compte_id = c.numero
LEFT JOIN mouvements m ON m.compte_id = c.numero
WHERE (COALESCE(s.solde_depart, 0) + COALESCE(m.total_mouvements, 0)) <> 0;

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
"""


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0024_sql_view_compagnie_ledger'),
    ]

    operations = [
        migrations.RunSQL(
            sql=DROP_VIEWS_SQL,
            reverse_sql=CREATE_VIEWS_SQL,
        ),
        migrations.AddField(
            model_name='tr_desc',
            name='note_de_credit',
            field=models.BooleanField(default=False),
        ),
        migrations.RunSQL(
            sql=CREATE_VIEWS_SQL,
            reverse_sql=DROP_VIEWS_SQL,
        ),
    ]