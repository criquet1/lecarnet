"""Vue petite_caisse_vue : meme principe que la vue `factures`, mais pour la
source 'Petite caisse'. Sert a lister/consulter les transactions de petite
caisse deja passees, detail par compte, sans avoir besoin d'un fournisseur
fictif "Petite caisse" (voir discussion dans la conversation)."""

from django.db import migrations, models

SQL_CREATE_VIEW = """
CREATE OR REPLACE VIEW public.petite_caisse_vue AS
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
WHERE t.source ILIKE 'petite caisse'
ORDER BY t.date, t.no_ej, t.compte_numero;
"""

SQL_DROP_VIEW = "DROP VIEW IF EXISTS public.petite_caisse_vue;"


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0036_inclure_facture_photo_dans_vue_factures'),
    ]

    operations = [
        migrations.CreateModel(
            name='PetiteCaisseVue',
            fields=[
                ('transaction_id', models.IntegerField(primary_key=True, serialize=False)),
                ('no_ej', models.CharField(max_length=30)),
                ('date', models.DateField()),
                ('compagnie', models.CharField(max_length=255, null=True)),
                ('description', models.CharField(max_length=255, null=True)),
                ('source', models.CharField(max_length=255, null=True)),
                ('compte_numero', models.IntegerField()),
                ('compte_libelle', models.CharField(max_length=255)),
                ('rapport_taxes_id', models.IntegerField(null=True)),
                ('debit', models.DecimalField(decimal_places=2, max_digits=12)),
                ('credit', models.DecimalField(decimal_places=2, max_digits=12)),
            ],
            options={
                'db_table': 'petite_caisse_vue',
                'managed': False,
            },
        ),
        migrations.RunSQL(
            sql=SQL_CREATE_VIEW,
            reverse_sql=SQL_DROP_VIEW,
        ),
    ]
