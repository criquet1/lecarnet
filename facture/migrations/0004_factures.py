from django.db import migrations, models

SQL_CREATE_VIEW = """
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

SQL_DROP_VIEW = "DROP VIEW IF EXISTS public.factures;"


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0003_transactions_liste'),
    ]

    operations = [
        migrations.CreateModel(
            name='Facture',
            fields=[
                ('transaction_id', models.IntegerField(primary_key=True, serialize=False)),
                ('no_ej', models.IntegerField()),
                ('date', models.DateField()),
                ('compagnie', models.CharField(max_length=255, null=True)),
                ('description', models.CharField(max_length=255, null=True)),
                ('source', models.CharField(max_length=255, null=True)),
                ('compte_numero', models.CharField(max_length=50)),
                ('compte_libelle', models.CharField(max_length=255)),
                ('rapport_taxes_id', models.IntegerField(null=True)),
                ('debit', models.DecimalField(decimal_places=2, max_digits=12)),
                ('credit', models.DecimalField(decimal_places=2, max_digits=12)),
            ],
            options={
                'db_table': 'factures',
                'managed': False,
            },
        ),
        migrations.RunSQL(
            sql=SQL_CREATE_VIEW,
            reverse_sql=SQL_DROP_VIEW,
        ),
    ]
