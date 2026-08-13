from django.db import migrations, models


def create_view(apps, schema_editor):
    if schema_editor.connection.alias == 'default':
        return
    schema_editor.execute(
        """
        CREATE OR REPLACE VIEW paie_journal_lignes AS
        SELECT
            p.id AS paie_id,
            e.nom AS employe_nom,
            e.prenom AS employe_prenom,
            pp.date_fin AS date_fin,
            p.salaire_brut_periode AS brut,
            p.salaire_net AS net
        FROM paie_paie p
        JOIN paie_employe e ON e.id = p.employe_id
        JOIN paie_periodepaie pp ON pp.id = p.periode_id;
        """
    )


def drop_view(apps, schema_editor):
    if schema_editor.connection.alias == 'default':
        return
    schema_editor.execute("DROP VIEW IF EXISTS paie_journal_lignes;")


class Migration(migrations.Migration):

    dependencies = [
        ('paie', '0008_paie_ae_employeur_paie_cnesst_employeur_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(create_view, reverse_code=drop_view),
            ],
            state_operations=[
                migrations.CreateModel(
                    name='PaieJournalLigne',
                    fields=[
                        ('paie_id', models.IntegerField(primary_key=True, serialize=False)),
                        ('employe_nom', models.CharField(max_length=100)),
                        ('employe_prenom', models.CharField(max_length=100)),
                        ('date_fin', models.DateField()),
                        ('brut', models.DecimalField(max_digits=10, decimal_places=2)),
                        ('net', models.DecimalField(max_digits=10, decimal_places=2)),
                    ],
                    options={
                        'db_table': 'paie_journal_lignes',
                        'managed': False,
                    },
                ),
            ],
        ),
    ]