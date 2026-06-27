from facture.models import Setting


def site_settings(request):
    settings = Setting.objects.first()

    if settings:
        request.session['nom'] = settings.nom
        request.session['logo'] = settings.logo or 'images/logos/images.png'
        request.session['phone'] = settings.phone
        request.session['adresse'] = settings.adresse
        request.session['ville'] = settings.ville
        request.session['code_postal'] = settings.code_postal
        request.session['pays'] = settings.pays
        request.session['email'] = settings.email
        request.session['compte_tps'] = settings.compte_tps_percue_id
        request.session['compte_tvq'] = settings.compte_tvq_percue_id
        request.session['compte_tps_payee'] = settings.compte_tps_payee_id
        request.session['compte_tvq_payee'] = settings.compte_tvq_payee_id
        request.session['compte_fr_retard'] = settings.compte_fr_retard_id
        request.session['compte_tps_label'] = str(settings.compte_tps_percue) if settings.compte_tps_percue else ''
        request.session['compte_tvq_label'] = str(settings.compte_tvq_percue) if settings.compte_tvq_percue else ''
        request.session['compte_fr_retard_label'] = str(settings.compte_fr_retard) if settings.compte_fr_retard else ''

    return {'site_settings': settings}