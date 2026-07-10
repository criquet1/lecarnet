from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0003_setting_date_debut_periode_paie_annee_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='setting',
            name='taux_cnesst_employeur',
            field=models.DecimalField(
                blank=True,
                decimal_places=5,
                max_digits=7,
                null=True,
                verbose_name='Taux CNESST employeur',
            ),
        ),
    ]
