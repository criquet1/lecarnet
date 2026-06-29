from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import BaseCommand, CommandError, call_command
from django.test import Client
from django.urls import reverse

from tenancy.services import (
    SESSION_CLIENT_ALIAS_KEY,
    SESSION_CLIENT_ID_KEY,
    get_user_client_accesses,
    pick_default_access,
)


class Command(BaseCommand):
    help = "Validation finale de parcours (phase 3.3): healthcheck + pages cles authentifiees."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default="admin",
            help="Utilisateur a utiliser pour le parcours (defaut: admin).",
        )
        parser.add_argument(
            "--client-alias",
            default="",
            help="Alias tenant cible (sinon client par defaut de l'utilisateur).",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Echoue aussi si healthcheck retourne des warnings.",
        )

    def _resolve_access(self, user, requested_alias):
        accesses = get_user_client_accesses(user)
        if not accesses.exists():
            raise CommandError(f"Utilisateur sans acces tenant actif: {user.username}")

        if requested_alias:
            access = accesses.filter(client__db_alias=requested_alias).first()
            if not access:
                raise CommandError(
                    f"Aucun acces pour {user.username} sur l'alias demande: {requested_alias}"
                )
            return access

        access = pick_default_access(accesses)
        if not access:
            raise CommandError(f"Impossible de resoudre un client par defaut pour {user.username}")
        return access

    def _assert_status(self, response, expected_statuses, label, failures):
        if response.status_code not in expected_statuses:
            failures.append(f"{label}: statut {response.status_code}, attendu {sorted(expected_statuses)}")

    def handle(self, *args, **options):
        strict = options.get("strict", False)
        username = (options.get("username") or "").strip()
        requested_alias = (options.get("client_alias") or "").strip()

        self.stdout.write(self.style.NOTICE("[1/3] Healthcheck multi-tenant..."))
        healthcheck_args = ["healthcheck_multitenant"]
        if strict:
            healthcheck_args.append("--fail-on-warn")

        try:
            call_command(*healthcheck_args)
        except CommandError as exc:
            raise CommandError(f"Healthcheck KO: {exc}")

        self.stdout.write(self.style.NOTICE("[2/3] Preparation utilisateur/session..."))
        User = get_user_model()
        user = User.objects.filter(username=username).first()
        if not user:
            raise CommandError(f"Utilisateur introuvable: {username}")
        if not user.is_active:
            raise CommandError(f"Utilisateur inactif: {username}")

        access = self._resolve_access(user, requested_alias)

        # Evite DisallowedHost dans le client de test Django.
        if "testserver" not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ["testserver"]

        client = Client()
        client.force_login(user)

        session = client.session
        session[SESSION_CLIENT_ID_KEY] = access.client_id
        session[SESSION_CLIENT_ALIAS_KEY] = access.client.db_alias
        session.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"- session preparee: user={user.username}, tenant={access.client.db_alias}"
            )
        )

        self.stdout.write(self.style.NOTICE("[3/3] Verification pages cles..."))
        checks = [
            ("accueil", reverse("accueil"), {200}),
            ("facture", reverse("facture"), {200}),
            ("releve", reverse("releve_bancaire"), {200}),
            ("journal_general", reverse("journal_general"), {200}),
            ("grand_livre", reverse("grand_livre"), {200}),
            ("balance", reverse("balance_de_verification"), {200}),
            ("compte_a_payer", reverse("compte_a_payer"), {200}),
            ("compte_a_recevoir", reverse("compte_a_recevoir"), {200}),
            ("rapport_de_taxes", reverse("rapport_de_taxes"), {200}),
            ("comptes", reverse("compte"), {200}),
            ("comptes_settings", reverse("settings"), {200}),
            ("select_client", reverse("select_client"), {200}),
        ]

        failures = []
        for label, url, expected in checks:
            response = client.get(url)
            self._assert_status(response, expected, label, failures)
            if response.status_code in expected:
                self.stdout.write(self.style.SUCCESS(f"- {label}: OK ({response.status_code})"))
            else:
                self.stdout.write(self.style.ERROR(f"- {label}: KO ({response.status_code})"))

        if failures:
            self.stdout.write(self.style.ERROR("Echecs parcours:"))
            for item in failures:
                self.stdout.write(f"  - {item}")
            raise CommandError("Validation parcours KO.")

        self.stdout.write(self.style.SUCCESS("Validation parcours OK."))
