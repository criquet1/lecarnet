from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand

from compte.models import Setting, ExerciceFinancier
from facture.helpers.dates import prochaine_date_fin_exercice


class Command(BaseCommand):
    help = "Cree le premier exercice financier historique pour chaque tenant, si aucun n'existe deja."

    def handle(self, *args, **options):
        today = date.today()

        for alias in settings.DATABASES.keys():
            if alias == 'default':
                continue

            if ExerciceFinancier.objects.using(alias).exists():
                self.stdout.write(f"{alias}: un exercice existe deja, ignore.")
                continue

            settings_instance = Setting.objects.using(alias).first()
            if not settings_instance:
                self.stdout.write(f"{alias}: aucun Setting trouve, ignore.")
                continue

            date_fin = prochaine_date_fin_exercice(today, settings_instance)
            exercice = ExerciceFinancier.creer_a_partir_de_la_date_fin(date_fin, alias=alias)
            self.stdout.write(self.style.SUCCESS(
                f"{alias}: exercice cree du {exercice.date_debut} au {exercice.date_fin}."
            ))