from django.db import migrations, models


class Migration(migrations.Migration):
    """Corrige le type de la colonne id de PetiteCaisseLigne.

    La migration 0029 avait ete ecrite par erreur avec un AutoField (int)
    plutot qu'un BigAutoField (bigint), qui est le standard utilise partout
    ailleurs dans ce projet (DEFAULT_AUTO_FIELD = BigAutoField). Cette
    migration corrige le type de colonne pour les bases ou 0029 a deja ete
    appliquee avec l'ancienne definition. Sans effet (ALTER vers le meme
    type) pour une base qui recevrait 0029 (deja corrigee) pour la premiere
    fois."""

    dependencies = [
        ('facture', '0029_petite_caisse_ligne'),
    ]

    operations = [
        migrations.AlterField(
            model_name='petitecaisseligne',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
    ]
