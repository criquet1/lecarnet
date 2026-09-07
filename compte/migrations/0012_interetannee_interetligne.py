# Generated manually (pas d'environnement Django disponible pour makemigrations)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0011_setting_logo_prive'),
    ]

    operations = [
        migrations.CreateModel(
            name='Preteur',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nom', models.CharField(max_length=120, unique=True)),
            ],
            options={
                'verbose_name': 'Prêteur (registre des intérêts)',
                'verbose_name_plural': 'Prêteurs (registre des intérêts)',
                'ordering': ['nom'],
            },
        ),
        migrations.CreateModel(
            name='InteretAnnee',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('annee', models.PositiveIntegerField()),
                ('taux', models.DecimalField(decimal_places=3, default=0, help_text="Taux d'intérêt annuel, en pourcentage, proraté par jour.", max_digits=6, verbose_name='Taux annuel (%)')),
                ('solde_initial', models.DecimalField(decimal_places=2, default=0, help_text="Solde à rembourser reporté de l'année précédente, pour ce prêteur (0 si aucun report).", max_digits=12, verbose_name='Solde reporté au 1er janvier')),
                ('preteur', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='annees', to='compte.preteur')),
            ],
            options={
                'verbose_name': 'Année (registre des intérêts)',
                'verbose_name_plural': 'Années (registre des intérêts)',
                'ordering': ['preteur__nom', 'annee'],
            },
        ),
        migrations.CreateModel(
            name='InteretLigne',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ordre', models.PositiveIntegerField(default=0, help_text="Ordre chronologique d'affichage des lignes.")),
                ('date_montant', models.DateField(blank=True, null=True, verbose_name='Date (montant prêté)')),
                ('montant', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='Montant prêté')),
                ('date_remboursement', models.DateField(blank=True, null=True, verbose_name='Date (remboursement)')),
                ('remboursement', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('annee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lignes', to='compte.interetannee')),
            ],
            options={
                'verbose_name': 'Ligne (registre des intérêts)',
                'verbose_name_plural': 'Lignes (registre des intérêts)',
                'ordering': ['annee', 'ordre', 'id'],
            },
        ),
        migrations.AddConstraint(
            model_name='interetannee',
            constraint=models.UniqueConstraint(fields=('preteur', 'annee'), name='une_annee_par_preteur'),
        ),
    ]
