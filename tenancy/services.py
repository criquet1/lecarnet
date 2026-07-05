from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError

from .models import ClientDatabase, UserClientAccess, UserSecurityState


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


def mark_user_must_change_password(user, required=True):
    try:
        state, _ = UserSecurityState.objects.get_or_create(user=user)
        if state.must_change_password != required:
            state.must_change_password = required
            state.save(update_fields=['must_change_password'])
        return state
    except (OperationalError, ProgrammingError):
        return None


def user_must_change_password(user):
    if not user.is_authenticated:
        return False

    try:
        return bool(user.security_state.must_change_password)
    except (UserSecurityState.DoesNotExist, OperationalError, ProgrammingError):
        return False


def _ensure_client_for_alias(alias):
    slug = alias.lower().replace('_', '-')
    name = alias.replace('_', ' ').title()

    client, _ = ClientDatabase.objects.get_or_create(
        db_alias=alias,
        defaults={
            'slug': slug,
            'name': name,
            'is_active': True,
        },
    )
    return client


def sync_user_client_accesses(user):
    if not user.is_authenticated:
        return UserClientAccess.objects.none()

    # Ne pas donner automatiquement tous les tenants aux utilisateurs standards.
    if not user.is_superuser:
        return get_user_client_accesses(user)

    active_clients = ClientDatabase.objects.filter(is_active=True).order_by('name', 'id')
    has_default = UserClientAccess.objects.filter(user=user, is_default=True).exists()

    for index, client in enumerate(active_clients):
        access, created = UserClientAccess.objects.get_or_create(
            user=user,
            client=client,
            defaults={'is_default': (index == 0 and not has_default)},
        )

        if created and index == 0 and not has_default:
            has_default = True

        if not created and not has_default and index == 0 and not access.is_default:
            access.is_default = True
            access.save(update_fields=['is_default'])
            has_default = True

    return get_user_client_accesses(user)


def ensure_default_client_access(user):
    if not user.is_authenticated:
        return None

    accesses = get_user_client_accesses(user)
    if accesses.exists():
        return pick_default_access(accesses)

    client = ClientDatabase.objects.filter(is_active=True).order_by('name', 'id').first()
    if not client:
        return None

    has_default = UserClientAccess.objects.filter(user=user, is_default=True).exists()
    access, created = UserClientAccess.objects.get_or_create(
        user=user,
        client=client,
        defaults={'is_default': not has_default},
    )

    if not created and not has_default and not access.is_default:
        access.is_default = True
        access.save(update_fields=['is_default'])

    return access
