from .services import get_user_client_accesses


def active_client(request):
    client = getattr(request, 'active_client', None)
    accesses = []

    if getattr(request, 'user', None) and request.user.is_authenticated:
        accesses = list(get_user_client_accesses(request.user).order_by('client__name'))

    return {
        'active_client': client,
        'active_client_accesses': accesses,
    }
