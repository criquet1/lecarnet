from django.db import migrations, models


SQL_DROP_VIEWS = """
DROP VIEW IF EXISTS public.factures;
DROP VIEW IF EXISTS public.transactions_liste;
"""

SQL_CREATE_VIEWS = """
CREATE OR REPLACE VIEW public.transactions_liste AS
SELECT
    t.id AS transaction_id,
    d.no_ej,
    d.date,
    c.nom AS compagnie,
    d.desc_ctb AS description,
    s.nom AS source,
    cc.numero AS compte_numero,
    cc.libelle AS compte_libelle,
    t.rapport_taxes_id,
    CASE WHEN t.montant >= 0 THEN t.montant ELSE 0 END AS debit,
    CASE WHEN t.montant < 0 THEN ABS(t.montant) ELSE 0 END AS credit
FROM facture_tr_detail t
JOIN facture_tr_desc d ON d.id = t.tr_desc_id
LEFT JOIN facture_compagnie c ON c.id = d.compagnie_id
LEFT JOIN facture_source s ON s.id = d.source_id
JOIN compte_compte cc ON cc.numero = t.compte_id;

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
FROM public.transactions_liste t
WHERE t.source ILIKE 'facture'
ORDER BY t.date, t.no_ej, t.compte_numero;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0008_normalize_statement_amounts'),
    ]

    operations = [
        migrations.RunSQL(
            sql=SQL_DROP_VIEWS,
            reverse_sql=SQL_CREATE_VIEWS,
        ),
        migrations.AlterField(
            model_name='source',
            name='nom',
            field=models.CharField(max_length=30),
        ),
        migrations.RunSQL(
            sql=SQL_CREATE_VIEWS,
            reverse_sql=SQL_DROP_VIEWS,
        ),
    ]