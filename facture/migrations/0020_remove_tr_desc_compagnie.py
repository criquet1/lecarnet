from django.db import migrations, models
import django.db.models.deletion

SQL_DROP_VIEWS = """
DROP VIEW IF EXISTS factures CASCADE;
DROP VIEW IF EXISTS transactions_liste CASCADE;
"""

SQL_RECREATE_TRANSACTIONS_LISTE = """
CREATE OR REPLACE VIEW transactions_liste AS
SELECT
    t.id AS transaction_id,
    d.no_ej AS no_ej,
    d.date AS date,
    COALESCE(cl.nom, fo.nom) AS compagnie,
    d.desc_ctb AS description,
    s.nom AS source,
    cc.numero AS compte_numero,
    cc.libelle AS compte_libelle,
    t.rapport_taxes_id AS rapport_taxes_id,
    CASE WHEN t.montant < 0 THEN ABS(t.montant) ELSE 0 END AS debit,
    CASE WHEN t.montant > 0 THEN t.montant ELSE 0 END AS credit
FROM facture_tr_detail t
JOIN facture_tr_desc d ON d.id = t.tr_desc_id
LEFT JOIN facture_client cl ON cl.id = d.client_id
LEFT JOIN facture_fournisseur fo ON fo.id = d.fournisseur_id
LEFT JOIN facture_source s ON s.id = d.source_id
JOIN compte_compte cc ON cc.numero = t.compte_id
ORDER BY d.date, d.no_ej, t.id;
"""

SQL_RECREATE_FACTURES = """
CREATE OR REPLACE VIEW public.factures AS
SELECT
    t.transaction_id,
    t.no_ej,
    t.date,
    t.compagnie,
    t.description,
    t.source,
    t.compte_numero,
    t.compte_libelle,
    t.rapport_taxes_id,
    t.debit,
    t.credit
FROM transactions_liste t
WHERE t.source ILIKE 'facture'
ORDER BY t.date, t.no_ej, t.compte_numero;
"""

SQL_REVERT_DROP = """
DROP VIEW IF EXISTS factures CASCADE;
DROP VIEW IF EXISTS transactions_liste CASCADE;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0019_update_transactions_liste_view'),
    ]

    operations = [
        migrations.RunSQL(
            sql=SQL_DROP_VIEWS,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RemoveField(
            model_name='tr_desc',
            name='compagnie',
        ),
        migrations.RunSQL(
            sql=SQL_RECREATE_TRANSACTIONS_LISTE + SQL_RECREATE_FACTURES,
            reverse_sql=SQL_REVERT_DROP,
        ),
    ]