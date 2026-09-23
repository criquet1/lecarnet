# Generated manually (pas d'environnement Django disponible pour makemigrations)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0016_supprimer_colonnes_reportee_orphelines'),
    ]

    operations = [
        migrations.AddField(
            model_name='interetligne',
            name='numero_pret',
            field=models.PositiveIntegerField(blank=True, db_index=True, null=True, verbose_name='Numéro de prêt'),
        ),
    ]
