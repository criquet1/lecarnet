from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('paie', '0009_parametrestauxpaie_exemption_base_rrq'),
    ]

    operations = [
        migrations.AddField(
            model_name='parametrestauxpaie',
            name='max_supplementaire_rrq',
            field=models.DecimalField(decimal_places=2, default=85000.0, max_digits=12),
        ),
        migrations.AddField(
            model_name='parametrestauxpaie',
            name='taux_rrq_supplementaire_2_employe',
            field=models.DecimalField(decimal_places=5, default=4.0, max_digits=7),
        ),
    ]
