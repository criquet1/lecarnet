from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0004_setting_taux_cnesst_employeur'),
    ]

    operations = [
        migrations.AddField(
            model_name='setting',
            name='taux_fss_employeur',
            field=models.DecimalField(
                blank=True,
                decimal_places=5,
                max_digits=7,
                null=True,
                verbose_name='Taux FSS employeur',
            ),
        ),
    ]
