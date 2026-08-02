from .services import get_user_client_accesses, is_expert


def active_client(request):
    client = getattr(request, 'active_client', None)
    accesses = []
    can_switch_tenant = False

    if getattr(request, 'user', None) and request.user.is_authenticated:
        accesses = list(get_user_client_accesses(request.user).order_by('client__name'))
        can_switch_tenant = is_expert(request.user)

    return {
        'active_client': client,
        'active_client_accesses': accesses,
        'can_switch_tenant': can_switch_tenant,
    }
