from django.db import migrations

SQL_CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION solde_fin_pour_exercice(p_exercice_id integer)
RETURNS TABLE(
    compte_numero integer,
    solde_depart numeric,
    total_transactions numeric,
    solde_final numeric
) AS $$
DECLARE
    v_date_debut date;
    v_date_fin date;
BEGIN
    SELECT date_debut, date_fin INTO v_date_debut, v_date_fin
    FROM compte_exercicefinancier WHERE id = p_exercice_id;

    RETURN QUERY
    SELECT
        cc.numero,
        COALESCE(sal.solde_depart, 0) + COALESCE(avant.total, 0) AS solde_depart,
        COALESCE(dans.total, 0) AS total_transactions,
        CASE
            WHEN cc.numero BETWEEN 1000 AND 3999 THEN COALESCE(sal.solde_depart,0) + COALESCE(avant.total,0) + COALESCE(dans.total,0)
            ELSE COALESCE(dans.total, 0)
        END AS solde_final
    FROM compte_compte cc
    LEFT JOIN compte_soldeauxlivres sal ON sal.compte_id = cc.numero
    LEFT JOIN LATERAL (
        SELECT SUM(td.montant) AS total
        FROM facture_tr_detail td
        JOIN facture_tr_desc d ON d.id = td.tr_desc_id
        WHERE td.compte_id = cc.numero AND d.date < v_date_debut
    ) avant ON true
    LEFT JOIN LATERAL (
        SELECT SUM(td.montant) AS total
        FROM facture_tr_detail td
        JOIN facture_tr_desc d ON d.id = td.tr_desc_id
        WHERE td.compte_id = cc.numero AND d.date BETWEEN v_date_debut AND v_date_fin
    ) dans ON true;
END;
$$ LANGUAGE plpgsql;
"""

SQL_DROP_FUNCTION = "DROP FUNCTION IF EXISTS solde_fin_pour_exercice(integer);"


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0022_hide_cloture_exercice_from_transactions_liste'),
        ('compte', '0007_remove_exercicefinancier_un_seul_exercice_ouvert_a_la_fois_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql=SQL_CREATE_FUNCTION,
            reverse_sql=SQL_DROP_FUNCTION,
        ),
    ]