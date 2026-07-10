from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('paie', '0012_employe_taux_vacances'),
    ]

    operations = [
        migrations.AddField(
            model_name='paie',
            name='vacances',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10),
        ),
    ]
