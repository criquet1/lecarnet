# Generated manually to match compte/models.py Setting.fin_annee_jour_semaine

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0009_alter_setting_taxes_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='setting',
            name='fin_annee_jour_semaine',
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(6),
                ],
                verbose_name="Jour de semaine de fin d'exercice",
                help_text=(
                    "Optionnel. Si rempli, l'exercice se termine le jour de semaine "
                    "choisi le plus proche du jour/mois ci-dessus (ex.: le samedi le "
                    "plus proche du 30 septembre), plutôt qu'à la date fixe. "
                    "0=lundi ... 6=dimanche."
                ),
            ),
        ),
    ]
