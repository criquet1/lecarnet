from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('paie', '0013_paie_vacances'),
    ]

    operations = [
        migrations.AddField(
            model_name='parametrestauxpaie',
            name='taux_cnt_employeur',
            field=models.DecimalField(decimal_places=5, default=Decimal('0.06000'), max_digits=7),
        ),
    ]
