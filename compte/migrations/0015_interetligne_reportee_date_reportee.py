# Migration annulée : la fonctionnalité "reportee/date_reportee" a été retirée
# avant d'être utilisée (revenue en arrière sur une mauvaise piste). Fichier
# neutralisé (plutôt que supprimé) pour ne pas briser la chaîne de migrations
# si "migrate" a déjà été lancé entre-temps.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0014_interetannee_interet_reporte'),
    ]

    operations = []
