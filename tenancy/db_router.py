import sys

from .db_context import get_current_tenant_alias


class TenantDatabaseRouter:
    """Route les apps metier vers la base du client actif."""

    tenant_app_labels = {'compte', 'facture', 'paie'}
    central_app_labels = {'auth', 'admin', 'contenttypes', 'sessions', 'tenancy'}
    centralized_tenant_models = {('paie', 'parametrestauxpaie')}

    @classmethod
    def _is_centralized_tenant_model(cls, app_label, model_name):
        if not model_name:
            return False
        return (app_label, model_name.lower()) in cls.centralized_tenant_models

    def db_for_read(self, model, **hints):
        if self._is_centralized_tenant_model(model._meta.app_label, model._meta.model_name):
            return 'default'
        if model._meta.app_label in self.tenant_app_labels:
            return get_current_tenant_alias() or 'default'
        return None

    def db_for_write(self, model, **hints):
        if self._is_centralized_tenant_model(model._meta.app_label, model._meta.model_name):
            return 'default'
        if model._meta.app_label in self.tenant_app_labels:
            return get_current_tenant_alias() or 'default'
        return None

    def allow_relation(self, obj1, obj2, **hints):
        app_labels = {obj1._meta.app_label, obj2._meta.app_label}
        if app_labels.issubset(self.tenant_app_labels):
            return True
        if app_labels.issubset(self.central_app_labels):
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        running_tests = 'test' in sys.argv
        if app_label == 'paie' and model_name is None:
            # Les operations RunPython de paie (sans model_name) doivent pouvoir
            # s'executer aussi sur default pour centraliser ParametresTauxPaie.
            return True
        if self._is_centralized_tenant_model(app_label, model_name):
            return db == 'default' or running_tests
        if app_label in self.tenant_app_labels:
            return db != 'default' or running_tests
        if app_label in self.central_app_labels:
            return db == 'default'
        return None
