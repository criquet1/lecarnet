from django.db import migrations, models


SQL_CREATE_TRANSACTIONS_VIEW = """
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
"""


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0006_comptereleve_actif'),
    ]

    operations = [
        migrations.RunSQL(
            sql=SQL_CREATE_TRANSACTIONS_VIEW,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name='transactionliste',
            name='no_ej',
            field=models.CharField(max_length=30),
        ),
        migrations.AlterField(
            model_name='transactionliste',
            name='compte_numero',
            field=models.IntegerField(),
        ),
        migrations.AlterField(
            model_name='facture',
            name='no_ej',
            field=models.CharField(max_length=30),
        ),
        migrations.AlterField(
            model_name='facture',
            name='compte_numero',
            field=models.IntegerField(),
        ),
    ]