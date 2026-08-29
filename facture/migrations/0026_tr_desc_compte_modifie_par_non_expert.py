from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0025_fix_transactions_liste_debit_credit_swap'),
    ]

    operations = [
        migrations.AddField(
            model_name='tr_desc',
            name='compte_modifie_par_non_expert',
            field=models.BooleanField(
                default=False,
                help_text="Vrai si un utilisateur non-expert a choisi un compte different de celui propose par defaut pour cette facture.",
            ),
        ),
    ]
