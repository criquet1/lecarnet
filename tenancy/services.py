from .models import UserClientAccess


SESSION_CLIENT_ID_KEY = 'active_client_id'
SESSION_CLIENT_ALIAS_KEY = 'active_client_alias'


def get_user_client_accesses(user):
    if not user.is_authenticated:
        return UserClientAccess.objects.none()
    return UserClientAccess.objects.select_related('client').filter(
        user=user,
        client__is_active=True,
    )


def pick_default_access(accesses):
    ordered = list(accesses.order_by('-is_default', 'client__name', 'id'))
    return ordered[0] if ordered else None


def set_active_client_on_session(request, access):
    request.session[SESSION_CLIENT_ID_KEY] = access.client_id
    request.session[SESSION_CLIENT_ALIAS_KEY] = access.client.db_alias


def clear_active_client_on_session(request):
    request.session.pop(SESSION_CLIENT_ID_KEY, None)
    request.session.pop(SESSION_CLIENT_ALIAS_KEY, None)
