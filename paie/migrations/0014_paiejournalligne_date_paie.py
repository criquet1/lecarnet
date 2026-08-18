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
            p.impot_provincial AS impot_provincial,
            pp.date_paie AS date_paie
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


class Migration(migrations.Migration):

    dependencies = [
        ('paie', '0013_parametrestauxpaie_max_ae_credit_federal_qc_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(update_view, reverse_code=revert_view),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='paiejournalligne',
                    name='date_paie',
                    field=models.DateField(null=True, blank=True),
                ),
            ],
        ),
    ]