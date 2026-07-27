# paie/management/commands/migrate_all_tenants.py

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Applique 'migrate' sur toutes les bases définies dans settings.DATABASES."

    def add_arguments(self, parser):
        parser.add_argument(
            '--noinput', action='store_true',
            help="Passe --noinput à chaque migrate (utile en script/CI)."
        )

    def handle(self, *args, **options):
        noms_bases = list(settings.DATABASES.keys())
        self.stdout.write(f"{len(noms_bases)} base(s) trouvée(s) : {noms_bases}")

        for nom_base in noms_bases:
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n--- Migration : {nom_base} ---"))
            try:
                call_command('migrate', database=nom_base, interactive=not options['noinput'])
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"Échec sur '{nom_base}' : {exc}"))
                # On continue sur les autres bases plutôt que de tout arrêter
                continue

        self.stdout.write(self.style.SUCCESS("\nMigration terminée pour toutes les bases."))