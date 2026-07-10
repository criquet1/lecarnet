from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('paie', '0011_parametrestauxpaie_abattement_federal_quebec_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='employe',
            name='taux_vacances',
            field=models.DecimalField(
                blank=True,
                decimal_places=5,
                default=Decimal('0.00000'),
                max_digits=7,
                null=True,
            ),
        ),
    ]
