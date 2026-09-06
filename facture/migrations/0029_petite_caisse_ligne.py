import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Table d'attente pour la compilation de petite caisse (onglet Banque).
    Voir facture/models.py::PetiteCaisseLigne pour le detail du mecanisme."""

    dependencies = [
        ('facture', '0028_logo_prive_en_base_de_donnees'),
        ('compte', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PetiteCaisseLigne',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('groupe', models.PositiveIntegerField(help_text="Relie les lignes issues d'un meme recu (ex.: un recu qui touche plusieurs comptes).")),
                ('date', models.DateField()),
                ('description', models.CharField(blank=True, default='', max_length=255)),
                ('montant', models.DecimalField(decimal_places=2, max_digits=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('compte', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='petite_caisse_lignes', to='compte.compte')),
            ],
            options={
                'ordering': ['groupe', 'id'],
            },
        ),
    ]
