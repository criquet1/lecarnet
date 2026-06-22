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
        request.session['compte_tps'] = settings.compte_tps_id
        request.session['compte_tvq'] = settings.compte_tvq_id
        request.session['compte_fr_retard'] = settings.compte_fr_retard_id
        request.session['compte_tps_label'] = str(settings.compte_tps)
        request.session['compte_tvq_label'] = str(settings.compte_tvq)
        request.session['compte_fr_retard_label'] = str(settings.compte_fr_retard)

    return {'site_settings': settings}