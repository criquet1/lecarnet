from django.db import migrations, models
from django.db.utils import OperationalError, ProgrammingError


def copy_global_dates_to_blocks(apps, schema_editor):
    ParametresTauxPaie = apps.get_model('paie', 'ParametresTauxPaie')
    table_names = schema_editor.connection.introspection.table_names()
    if ParametresTauxPaie._meta.db_table not in table_names:
        return

    try:
        rows = list(ParametresTauxPaie.objects.all())
    except (ProgrammingError, OperationalError):
        return

    for row in rows:
        start = row.date_debut_effet
        end = row.date_fin_effet

        row.rrq_date_debut_effet = start
        row.rrq_date_fin_effet = end
        row.rqap_date_debut_effet = start
        row.rqap_date_fin_effet = end
        row.ae_date_debut_effet = start
        row.ae_date_fin_effet = end
        row.fss_date_debut_effet = start
        row.fss_date_fin_effet = end
        row.annee_cnesst = start.year if start else None
        row.save(
            update_fields=[
                'rrq_date_debut_effet',
                'rrq_date_fin_effet',
                'rqap_date_debut_effet',
                'rqap_date_fin_effet',
                'ae_date_debut_effet',
                'ae_date_fin_effet',
                'fss_date_debut_effet',
                'fss_date_fin_effet',
                'annee_cnesst',
            ]
        )


class Migration(migrations.Migration):

    dependencies = [
        ('paie', '0003_parametrestauxpaie'),
    ]

    operations = [
        migrations.AddField(
            model_name='parametrestauxpaie',
            name='ae_date_debut_effet',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='parametrestauxpaie',
            name='ae_date_fin_effet',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='parametrestauxpaie',
            name='annee_cnesst',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='parametrestauxpaie',
            name='fss_date_debut_effet',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='parametrestauxpaie',
            name='fss_date_fin_effet',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='parametrestauxpaie',
            name='rqap_date_debut_effet',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='parametrestauxpaie',
            name='rqap_date_fin_effet',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='parametrestauxpaie',
            name='rrq_date_debut_effet',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='parametrestauxpaie',
            name='rrq_date_fin_effet',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RenameField(
            model_name='parametrestauxpaie',
            old_name='taux_csst_employeur',
            new_name='taux_cnesst_employeur',
        ),
        migrations.RunPython(copy_global_dates_to_blocks, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='parametrestauxpaie',
            name='ae_date_debut_effet',
            field=models.DateField(),
        ),
        migrations.AlterField(
            model_name='parametrestauxpaie',
            name='annee_cnesst',
            field=models.PositiveSmallIntegerField(),
        ),
        migrations.AlterField(
            model_name='parametrestauxpaie',
            name='fss_date_debut_effet',
            field=models.DateField(),
        ),
        migrations.AlterField(
            model_name='parametrestauxpaie',
            name='rqap_date_debut_effet',
            field=models.DateField(),
        ),
        migrations.AlterField(
            model_name='parametrestauxpaie',
            name='rrq_date_debut_effet',
            field=models.DateField(),
        ),
        migrations.AlterModelOptions(
            name='parametrestauxpaie',
            options={
                'ordering': ['-rrq_date_debut_effet', '-id'],
                'verbose_name': 'Parametres de taux de paie',
                'verbose_name_plural': 'Parametres de taux de paie',
            },
        ),
        migrations.RemoveField(
            model_name='parametrestauxpaie',
            name='date_debut_effet',
        ),
        migrations.RemoveField(
            model_name='parametrestauxpaie',
            name='date_fin_effet',
        ),
    ]
