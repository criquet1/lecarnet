from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0002_solde_fin'),
        ('compte', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
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
                    """,
                    reverse_sql="DROP VIEW IF EXISTS transactions_liste;",
                )
            ],
            state_operations=[
                migrations.CreateModel(
                    name='TransactionListe',
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
                        ('debit', models.DecimalField(max_digits=12, decimal_places=2)),
                        ('credit', models.DecimalField(max_digits=12, decimal_places=2)),
                    ],
                    options={
                        'db_table': 'transactions_liste',
                        'managed': False,
                    },
                ),
            ],
        ),
    ]