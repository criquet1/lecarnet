from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0005_setting_taux_fss_employeur'),
    ]

    operations = [
        migrations.AddField(
            model_name='setting',
            name='compte_benefices_marginaux',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='settings_benefices_marginaux',
                to='compte.compte',
            ),
        ),
        migrations.AddField(
            model_name='setting',
            name='compte_das_federales',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='settings_das_federales',
                to='compte.compte',
            ),
        ),
        migrations.AddField(
            model_name='setting',
            name='compte_das_provinciales',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='settings_das_provinciales',
                to='compte.compte',
            ),
        ),
        migrations.AddField(
            model_name='setting',
            name='compte_salaire',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='settings_salaire',
                to='compte.compte',
            ),
        ),
        migrations.AddField(
            model_name='setting',
            name='compte_salaires_a_payer',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='settings_salaires_a_payer',
                to='compte.compte',
            ),
        ),
        migrations.AddField(
            model_name='setting',
            name='compte_vacances_a_payer',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='settings_vacances_a_payer',
                to='compte.compte',
            ),
        ),
        migrations.AddField(
            model_name='setting',
            name='comptes_paie_autres',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Liste optionnelle de numeros de comptes supplementaires pour la paie.',
            ),
        ),
    ]
