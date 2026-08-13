from django.db import migrations, models


def update_view(apps, schema_editor):
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
            p.salaire_net AS net,
            p.rrq AS rrq_employe,
            p.rrq_employeur AS rrq_employeur,
            p.rqap AS rqap_employe,
            p.rqap_employeur AS rqap_employeur,
            p.ae AS ae_employe,
            p.ae_employeur AS ae_employeur,
            p.cnesst_employeur AS cnesst_employeur,
            p.fss_employeur AS fss_employeur,
            p.impot_federal AS impot_federal,
            p.impot_provincial AS impot_provincial
        FROM paie_paie p
        JOIN paie_employe e ON e.id = p.employe_id
        JOIN paie_periodepaie pp ON pp.id = p.periode_id;
        """
    )


def revert_view(apps, schema_editor):
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


class Migration(migrations.Migration):

    dependencies = [
        ('paie', '0009_vue_paie_journal_lignes'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(update_view, reverse_code=revert_view),
            ],
            state_operations=[
                migrations.AddField(model_name='paiejournalligne', name='rrq_employe', field=models.DecimalField(max_digits=10, decimal_places=2)),
                migrations.AddField(model_name='paiejournalligne', name='rrq_employeur', field=models.DecimalField(max_digits=10, decimal_places=2)),
                migrations.AddField(model_name='paiejournalligne', name='rqap_employe', field=models.DecimalField(max_digits=10, decimal_places=2)),
                migrations.AddField(model_name='paiejournalligne', name='rqap_employeur', field=models.DecimalField(max_digits=10, decimal_places=2)),
                migrations.AddField(model_name='paiejournalligne', name='ae_employe', field=models.DecimalField(max_digits=10, decimal_places=2)),
                migrations.AddField(model_name='paiejournalligne', name='ae_employeur', field=models.DecimalField(max_digits=10, decimal_places=2)),
                migrations.AddField(model_name='paiejournalligne', name='cnesst_employeur', field=models.DecimalField(max_digits=10, decimal_places=2)),
                migrations.AddField(model_name='paiejournalligne', name='fss_employeur', field=models.DecimalField(max_digits=10, decimal_places=2)),
                migrations.AddField(model_name='paiejournalligne', name='impot_federal', field=models.DecimalField(max_digits=10, decimal_places=2)),
                migrations.AddField(model_name='paiejournalligne', name='impot_provincial', field=models.DecimalField(max_digits=10, decimal_places=2)),
            ],
        ),
    ]