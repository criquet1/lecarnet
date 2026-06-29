def active_client(request):
    client = getattr(request, 'active_client', None)
    return {
        'active_client': client,
    }
