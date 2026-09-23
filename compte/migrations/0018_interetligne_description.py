# Generated manually (pas d'environnement Django disponible pour makemigrations)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0017_interetligne_numero_pret'),
    ]

    operations = [
        migrations.AddField(
            model_name='interetligne',
            name='description',
            field=models.CharField(blank=True, max_length=100, verbose_name='Description'),
        ),
    ]
