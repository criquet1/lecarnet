from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('paie', '0003_vue_periodes_paie'),
    ]

    operations = [
        migrations.AddField(
            model_name='paie',
            name='heures_supp',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                max_digits=7,
            ),
        ),
    ]