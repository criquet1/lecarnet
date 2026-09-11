# Generated manually (pas d'environnement Django disponible pour makemigrations)

from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0013_interetligne_interet_verse'),
    ]

    operations = [
        migrations.AddField(
            model_name='interetannee',
            name='interet_reporte',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), help_text="Intérêts courus l'année précédente mais non versés (courus moins versés) : s'ajoutent au calcul des intérêts courus de cette année.", max_digits=12, verbose_name='Intérêts non versés reportés'),
        ),
    ]
