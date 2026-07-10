from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('paie', '0008_ensure_central_parametrestauxpaie_table'),
    ]

    operations = [
        migrations.AddField(
            model_name='parametrestauxpaie',
            name='exemption_base_rrq',
            field=models.DecimalField(decimal_places=2, default=3500.0, max_digits=12),
        ),
    ]
