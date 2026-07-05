from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .services import get_user_client_accesses, set_active_client_on_session, sync_user_client_accesses


@login_required
def select_client(request):
    sync_user_client_accesses(request.user)
    accesses = get_user_client_accesses(request.user)
    if accesses.count() == 1:
        only_access = accesses.first()
        set_active_client_on_session(request, only_access)
        return redirect('accueil')

    return render(request, 'tenancy/select_client.html', {
        'title': 'Choisir un client',
        'accesses': accesses.order_by('client__name'),
    })


@login_required
@require_POST
def set_active_client(request):
    sync_user_client_accesses(request.user)
    accesses = get_user_client_accesses(request.user)
    access = get_object_or_404(accesses, client_id=request.POST.get('client_id'))
    set_active_client_on_session(request, access)
    return redirect(request.POST.get('next') or 'accueil')
