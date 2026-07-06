from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError

from .models import ClientDatabase, UserClientAccess, UserSecurityState, UserSocieteAccess


SESSION_CLIENT_ID_KEY = 'active_client_id'
SESSION_CLIENT_ALIAS_KEY = 'active_client_alias'


def _is_expert(user):
    return user.is_superuser or user.groups.filter(name__iexact='expert').exists()


def get_user_client_accesses(user):
    if not user.is_authenticated:
        return UserClientAccess.objects.none()

    base_qs = UserClientAccess.objects.select_related('client').filter(
        user=user,
        client__is_active=True,
    )

    if user.is_superuser:
        return base_qs

    allowed_societe_ids = UserSocieteAccess.objects.filter(
        user=user,
        societe__is_active=True,
    ).values_list('societe_id', flat=True)

    scoped_qs = base_qs.filter(client__societe_id__in=allowed_societe_ids)

    if _is_expert(user):
        return scoped_qs

    # Standard user: enforce single-tenant visibility.
    selected_id = scoped_qs.order_by('-is_default', 'client__name', 'id').values_list('id', flat=True).first()
    if not selected_id:
        return UserClientAccess.objects.none()

    return scoped_qs.filter(id=selected_id)


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

    if user.is_superuser:
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

    allowed_societe_ids = list(UserSocieteAccess.objects.filter(
        user=user,
        societe__is_active=True,
    ).values_list('societe_id', flat=True))

    if not allowed_societe_ids:
        UserClientAccess.objects.filter(user=user).delete()
        return UserClientAccess.objects.none()

    active_clients = ClientDatabase.objects.filter(
        is_active=True,
        societe_id__in=allowed_societe_ids,
    ).order_by('name', 'id')
    allowed_client_ids = list(active_clients.values_list('id', flat=True))

    UserClientAccess.objects.filter(user=user).exclude(client_id__in=allowed_client_ids).delete()

    if _is_expert(user):
        for client in active_clients:
            UserClientAccess.objects.get_or_create(
                user=user,
                client=client,
                defaults={'is_default': False},
            )

        accesses = UserClientAccess.objects.filter(
            user=user,
            client_id__in=allowed_client_ids,
        ).order_by('client__name', 'id')

        default_access = accesses.filter(is_default=True).first()
        if not default_access:
            default_access = accesses.first()
            if default_access:
                default_access.is_default = True
                default_access.save(update_fields=['is_default'])

        if default_access:
            accesses.exclude(id=default_access.id).filter(is_default=True).update(is_default=False)

        return get_user_client_accesses(user)

    # Standard user: keep only one existing allowed access, no auto-grant across all tenants.
    accesses = UserClientAccess.objects.filter(
        user=user,
        client_id__in=allowed_client_ids,
    ).order_by('-is_default', 'client__name', 'id')

    selected_access = accesses.first()
    if not selected_access:
        return UserClientAccess.objects.none()

    accesses.exclude(id=selected_access.id).delete()
    if not selected_access.is_default:
        selected_access.is_default = True
        selected_access.save(update_fields=['is_default'])

    return get_user_client_accesses(user)


def ensure_default_client_access(user):
    if not user.is_authenticated:
        return None

    if not user.is_superuser:
        sync_user_client_accesses(user)
        accesses = get_user_client_accesses(user)
        return pick_default_access(accesses)

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
