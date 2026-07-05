from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

from .db_context import reset_current_tenant_alias, set_current_tenant_alias
from .services import (
    SESSION_CLIENT_ID_KEY,
    clear_active_client_on_session,
    ensure_default_client_access,
    get_user_client_accesses,
    pick_default_access,
    set_active_client_on_session,
    sync_user_client_accesses,
    user_must_change_password,
)


class ActiveClientMiddleware:
    """Resout le client actif d'un utilisateur et expose son alias DB par requete."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = None
        request.active_client = None
        request.active_client_alias = None

        if request.user.is_authenticated:
            sync_user_client_accesses(request.user)
            accesses = get_user_client_accesses(request.user)
            access = None

            if accesses.exists():
                requested_client_id = request.session.get(SESSION_CLIENT_ID_KEY)
                if requested_client_id:
                    access = accesses.filter(client_id=requested_client_id).first()

                if not access:
                    access = pick_default_access(accesses)
            else:
                access = ensure_default_client_access(request.user)

            if access:
                set_active_client_on_session(request, access)
                alias = access.client.db_alias
                if alias in settings.DATABASES:
                    request.active_client = access.client
                    request.active_client_alias = alias
                    token = set_current_tenant_alias(alias)
                else:
                    clear_active_client_on_session(request)
            else:
                clear_active_client_on_session(request)

        exempt_paths = {
            reverse('login'),
            reverse('logout'),
            reverse('select_client'),
            reverse('set_active_client'),
            reverse('force_password_change'),
            reverse('user_password_change'),
        }

        if request.user.is_authenticated and user_must_change_password(request.user):
            if request.path not in exempt_paths and not request.path.startswith('/admin/'):
                if token is not None:
                    reset_current_tenant_alias(token)
                return redirect('force_password_change')

        if request.user.is_authenticated and not request.active_client:
            if request.path not in exempt_paths and not request.path.startswith('/admin/'):
                response = redirect('select_client')
                if token is not None:
                    reset_current_tenant_alias(token)
                return response

        response = self.get_response(request)

        if token is not None:
            reset_current_tenant_alias(token)

        return response
