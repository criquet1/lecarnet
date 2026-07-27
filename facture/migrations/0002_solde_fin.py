from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0001_initial'),
        ('compte', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                    CREATE OR REPLACE VIEW solde_fin AS
                    SELECT 
                        c.numero AS compte_numero,
                        COALESCE(s.solde_depart, 0) AS solde_depart,
                        COALESCE((
                            SELECT SUM(t.montant)
                            FROM facture_tr_detail t
                            WHERE t.compte_id = c.numero
                        ), 0) AS total_transactions,
                        COALESCE(s.solde_depart, 0) +
                        COALESCE((
                            SELECT SUM(t.montant)
                            FROM facture_tr_detail t
                            WHERE t.compte_id = c.numero
                        ), 0) AS solde_final
                    FROM compte_compte c
                    LEFT JOIN compte_soldeauxlivres s
                        ON s.compte_id = c.numero
                    ORDER BY c.numero;
                    """,
                    reverse_sql="DROP VIEW IF EXISTS solde_fin;",
                )
            ],
            state_operations=[
                migrations.CreateModel(
                    name='SoldeFin',
                    fields=[
                        ('compte_numero', models.OneToOneField(
                            db_column='compte_numero',
                            on_delete=django.db.models.deletion.DO_NOTHING,
                            primary_key=True,
                            serialize=False,
                            to='compte.compte',
                            to_field='numero',
                        )),
                        ('solde_depart', models.DecimalField(max_digits=10, decimal_places=2)),
                        ('total_transactions', models.DecimalField(max_digits=10, decimal_places=2)),
                        ('solde_final', models.DecimalField(max_digits=10, decimal_places=2)),
                    ],
                    options={
                        'db_table': 'solde_fin',
                        'ordering': ['compte_numero'],
                        'managed': False,
                    },
                ),
            ],
        ),
    ]