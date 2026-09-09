from django.db import migrations

SQL_CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION solde_conciliation_pour_periode(
    p_compte_id integer,
    p_date_debut date,
    p_date_fin_exclusive date
)
RETURNS TABLE(
    solde_depart numeric,
    total_debits numeric,
    total_credits numeric,
    solde_fin numeric
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        solde_depart_calc.solde_depart,
        COALESCE(mois.total_debits, 0) AS total_debits,
        COALESCE(mois.total_credits, 0) AS total_credits,
        solde_depart_calc.solde_depart + COALESCE(mois.total_debits, 0) - COALESCE(mois.total_credits, 0) AS solde_fin
    FROM (
        SELECT
            COALESCE((SELECT sal.solde_depart FROM compte_soldeauxlivres sal WHERE sal.compte_id = p_compte_id), 0)
            + COALESCE((
                SELECT SUM(td.montant)
                FROM facture_tr_detail td
                JOIN facture_tr_desc d ON d.id = td.tr_desc_id
                WHERE td.compte_id = p_compte_id AND d.date < p_date_debut
            ), 0) AS solde_depart
    ) solde_depart_calc
    LEFT JOIN LATERAL (
        SELECT
            SUM(CASE WHEN td.montant > 0 THEN td.montant ELSE 0 END) AS total_debits,
            SUM(CASE WHEN td.montant < 0 THEN -td.montant ELSE 0 END) AS total_credits
        FROM facture_tr_detail td
        JOIN facture_tr_desc d ON d.id = td.tr_desc_id
        WHERE td.compte_id = p_compte_id AND d.date >= p_date_debut AND d.date < p_date_fin_exclusive
    ) mois ON true;
END;
$$ LANGUAGE plpgsql;
"""

SQL_DROP_FUNCTION = "DROP FUNCTION IF EXISTS solde_conciliation_pour_periode(integer, date, date);"


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0030_alter_petitecaisseligne_id'),
    ]

    operations = [
        migrations.RunSQL(
            sql=SQL_CREATE_FUNCTION,
            reverse_sql=SQL_DROP_FUNCTION,
        ),
    ]
