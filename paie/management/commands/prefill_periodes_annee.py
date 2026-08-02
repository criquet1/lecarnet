import datetime

from django.core.management.base import BaseCommand

from paie.services.periodes import generate_periodes_annee
from tenancy.models import ClientDatabase


class Command(BaseCommand):
    help = (
        "Pré-remplit les PeriodePaie pour une année donnée dans les tenants actifs. "
        "À exécuter après la saisie des nouveaux taux annuels, ou pour un nouveau tenant."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--annee',
            type=int,
            default=datetime.date.today().year,
            help="Année à générer (défaut : année courante).",
        )
        parser.add_argument(
            '--tenant',
            type=str,
            default=None,
            metavar='ALIAS',
            help="Alias de base d'un tenant spécifique (si omis : tous les tenants actifs).",
        )

    def handle(self, *args, **options):
        annee = options['annee']
        tenant = options['tenant']

        if tenant:
            aliases = [tenant]
        else:
            aliases = list(
                ClientDatabase.objects.filter(is_active=True).values_list('db_alias', flat=True)
            )

        if not aliases:
            self.stdout.write(self.style.WARNING("Aucun tenant actif trouvé."))
            return

        self.stdout.write(
            self.style.MIGRATE_HEADING(f"Génération des périodes de paie pour {annee} ({len(aliases)} tenant(s))")
        )

        total = 0
        for alias in aliases:
            try:
                n = generate_periodes_annee(annee, alias)
                total += n
                if n:
                    self.stdout.write(f"  {alias}: {n} période(s) créée(s).")
                else:
                    self.stdout.write(f"  {alias}: déjà à jour.")
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  {alias}: erreur — {exc}"))

        self.stdout.write(self.style.SUCCESS(f"\nTotal : {total} période(s) créée(s)."))
