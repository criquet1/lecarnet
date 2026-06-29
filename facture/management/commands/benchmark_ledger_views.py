from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from facture.models import Setting


class Command(BaseCommand):
    help = "Mesure les temps SQL des vues comptables (grand livre, balance, CAP, CAR)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            default="client_test",
            help="Alias de base a cibler (ex: client_test, client_alpha).",
        )
        parser.add_argument(
            "--cap-account",
            type=int,
            default=None,
            help="Compte comptable CAP a forcer pour les tests scoped.",
        )
        parser.add_argument(
            "--car-account",
            type=int,
            default=None,
            help="Compte comptable CAR a forcer pour les tests scoped.",
        )

    def _run_explain(self, cursor, sql, params=None):
        explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {sql}"
        cursor.execute(explain_sql, params or [])
        rows = [r[0] for r in cursor.fetchall()]
        exec_lines = [line for line in rows if "Execution Time" in line]
        return exec_lines[-1] if exec_lines else "Execution Time: n/a"

    def _count(self, cursor, sql, params=None):
        cursor.execute(sql, params or [])
        return cursor.fetchone()[0]

    def handle(self, *args, **options):
        alias = options["database"]
        if alias not in settings.DATABASES:
            raise CommandError(f"Alias inconnu: {alias}")

        settings_row = Setting.objects.using(alias).first()
        cap_compte_id = options.get("cap_account") or (settings_row.cap_id if settings_row else None)
        car_compte_id = options.get("car_account") or (settings_row.car_id if settings_row else None)

        connection = connections[alias]
        with connection.cursor() as cursor:
            tests = [
                {
                    "name": "grand_livre",
                    "count_sql": "SELECT COUNT(*) FROM facture_v_grand_livre_lignes",
                    "query_sql": (
                        "SELECT * FROM facture_v_grand_livre_lignes "
                        "ORDER BY compte_numero, tr_date, no_ej, tr_desc_id, tr_detail_id"
                    ),
                    "params": [],
                },
                {
                    "name": "balance",
                    "count_sql": "SELECT COUNT(*) FROM facture_v_balance_verification",
                    "query_sql": "SELECT * FROM facture_v_balance_verification ORDER BY compte_numero",
                    "params": [],
                },
                {
                    "name": "cap",
                    "count_sql": "SELECT COUNT(*) FROM facture_v_compagnie_ledger_lignes WHERE cap_ou_car = %s",
                    "query_sql": (
                        "SELECT * FROM facture_v_compagnie_ledger_lignes "
                        "WHERE cap_ou_car = %s "
                        "ORDER BY compagnie_nom, tr_date, tr_desc_id, tr_detail_id"
                    ),
                    "params": ["CAP"],
                },
                {
                    "name": "cap_scoped",
                    "count_sql": "SELECT COUNT(*) FROM facture_v_compagnie_ledger_lignes WHERE cap_ou_car = %s AND compte_id = %s",
                    "query_sql": (
                        "SELECT * FROM facture_v_compagnie_ledger_lignes "
                        "WHERE cap_ou_car = %s AND compte_id = %s "
                        "ORDER BY compagnie_nom, tr_date, tr_desc_id, tr_detail_id"
                    ),
                    "params": ["CAP", cap_compte_id],
                    "skip_if": cap_compte_id is None,
                    "skip_reason": "Setting.cap non configure",
                },
                {
                    "name": "car",
                    "count_sql": "SELECT COUNT(*) FROM facture_v_compagnie_ledger_lignes WHERE cap_ou_car = %s",
                    "query_sql": (
                        "SELECT * FROM facture_v_compagnie_ledger_lignes "
                        "WHERE cap_ou_car = %s "
                        "ORDER BY compagnie_nom, tr_date, tr_desc_id, tr_detail_id"
                    ),
                    "params": ["CAR"],
                },
                {
                    "name": "car_scoped",
                    "count_sql": "SELECT COUNT(*) FROM facture_v_compagnie_ledger_lignes WHERE cap_ou_car = %s AND compte_id = %s",
                    "query_sql": (
                        "SELECT * FROM facture_v_compagnie_ledger_lignes "
                        "WHERE cap_ou_car = %s AND compte_id = %s "
                        "ORDER BY compagnie_nom, tr_date, tr_desc_id, tr_detail_id"
                    ),
                    "params": ["CAR", car_compte_id],
                    "skip_if": car_compte_id is None,
                    "skip_reason": "Setting.car non configure",
                },
            ]

            self.stdout.write(self.style.NOTICE(f"Benchmark SQL sur alias: {alias}"))
            for test in tests:
                if test.get("skip_if"):
                    self.stdout.write(f"- {test['name']}: ignore ({test.get('skip_reason', 'non applicable')})")
                    continue
                count = self._count(cursor, test["count_sql"], test["params"])
                timing = self._run_explain(cursor, test["query_sql"], test["params"])
                self.stdout.write(f"- {test['name']}: rows={count} | {timing}")
