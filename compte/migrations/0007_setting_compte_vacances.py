from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0006_setting_comptes_paie'),
    ]

    operations = [
        migrations.AddField(
            model_name='setting',
            name='compte_vacances',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='settings_vacances',
                to='compte.compte',
            ),
        ),
    ]
