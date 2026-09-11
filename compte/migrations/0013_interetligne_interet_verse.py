# Generated manually (pas d'environnement Django disponible pour makemigrations)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0012_interetannee_interetligne'),
    ]

    operations = [
        migrations.AddField(
            model_name='interetligne',
            name='interet_verse',
            field=models.DecimalField(blank=True, decimal_places=2, help_text="Montant d'intérêts réellement versé à cette date (n'affecte pas le solde à rembourser, contrairement à un remboursement de capital).", max_digits=12, null=True, verbose_name='Intérêts versés'),
        ),
    ]
