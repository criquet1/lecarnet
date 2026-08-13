# paie/management/commands/backfill_cotisations_employeur.py

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.utils import ProgrammingError

from paie.models import Paie
from tenancy.db_context import set_current_tenant_alias, reset_current_tenant_alias


class Command(BaseCommand):
    help = "Recalcule les paies existantes pour remplir les cotisations employeur (RRQ/RQAP/AE/CNESST/FSS)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Calcule et affiche seulement, ne sauvegarde rien."
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        noms_bases = list(settings.DATABASES.keys())

        for nom_base in noms_bases:
            try:
                paies = list(Paie.objects.using(nom_base).all())
            except ProgrammingError:
                continue
            if not paies:
                continue

            self.stdout.write(f"--- {nom_base} : {len(paies)} paie(s) ---")
            token = set_current_tenant_alias(nom_base)
            try:
                for paie in paies:
                    try:
                        paie.recalculer()
                        if not dry_run:
                            paie.save(using=nom_base)
                        statut = "sauvegardee" if not dry_run else "calculee (dry-run)"
                        self.stdout.write(f"  paie id={paie.id} : OK, {statut}")
                    except Exception as exc:
                        self.stdout.write(f"  paie id={paie.id} : ERREUR - {exc}")
            finally:
                reset_current_tenant_alias(token)