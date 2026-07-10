from django.conf import settings
from django.core.management import BaseCommand, call_command, CommandError


class Command(BaseCommand):
    help = 'Applique les migrations metier (compte/facture) sur les bases clientes.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--database',
            action='append',
            dest='databases',
            help='Alias de base client cible. Peut etre repete.',
        )

    def handle(self, *args, **options):
        aliases = options.get('databases') or [
            alias for alias in settings.DATABASES.keys() if alias != 'default'
        ]

        if not aliases:
            self.stdout.write(
                self.style.WARNING(
                    'Aucune base client configuree. Migration tenants ignoree.'
                )
            )
            return

        for alias in aliases:
            if alias == 'default':
                continue
            if alias not in settings.DATABASES:
                raise CommandError(f"Alias inconnu: {alias}")

            self.stdout.write(self.style.NOTICE(f"Migration de la base client: {alias}"))
            call_command('migrate', database=alias, interactive=False)

        self.stdout.write(self.style.SUCCESS('Migrations clients terminees.'))
