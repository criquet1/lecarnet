from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('paie', '0014_parametrestauxpaie_taux_cnt_employeur'),
    ]

    operations = [
        migrations.AddField(
            model_name='paie',
            name='vacances_payees',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10),
        ),
    ]
