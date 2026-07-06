from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('paie', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='employe',
            name='actif',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='employe',
            name='frequence_paie',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='employes', to='paie.frequencepaie'),
        ),
        migrations.CreateModel(
            name='PeriodePaie',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date_debut', models.DateField(blank=True, null=True)),
                ('date_fin', models.DateField()),
                ('date_paie', models.DateField(blank=True, null=True)),
                ('fermee', models.BooleanField(default=False)),
                ('frequence_paie', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='periodes', to='paie.frequencepaie')),
            ],
            options={
                'ordering': ['-date_fin', '-id'],
            },
        ),
        migrations.CreateModel(
            name='Paie',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('heures_travaillees', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=7)),
                ('taux_horaire', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('montant_personnel_federal_td1', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('montant_personnel_quebec_tp1015', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('deduction_code_f', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('deduction_tp1015_j', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('deduction_tp1016_j1', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('retenue_supplementaire_qc', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('cotisation_supplementaire_rrq_csa', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('salaire_brut_periode', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('rqap', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('rrq', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('ae', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('impot_federal', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('impot_provincial', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('total_retenues', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('salaire_net', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('cree_le', models.DateTimeField(auto_now_add=True)),
                ('modifie_le', models.DateTimeField(auto_now=True)),
                ('employe', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='paies', to='paie.employe')),
                ('periode', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='paies', to='paie.periodepaie')),
            ],
            options={
                'ordering': ['-periode__date_fin', '-id'],
            },
        ),
        migrations.AddConstraint(
            model_name='periodepaie',
            constraint=models.UniqueConstraint(fields=('frequence_paie', 'date_debut', 'date_fin'), name='paie_periode_unique_frequence_dates'),
        ),
        migrations.AddConstraint(
            model_name='paie',
            constraint=models.UniqueConstraint(fields=('employe', 'periode'), name='paie_unique_employe_periode'),
        ),
        migrations.AlterModelOptions(
            name='employe',
            options={'ordering': ['nom', 'prenom', 'id']},
        ),
    ]