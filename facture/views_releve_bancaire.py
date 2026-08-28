"""Vues et helpers liés au relevé bancaire : import CSV, rapprochement/suggestions,
détection de compte, et affichage de la page relevé bancaire.

Extrait de facture/views.py pour alléger ce fichier (les fonctions privées
préfixées par _ sont des helpers internes, non réutilisés ailleurs).
"""

from django.shortcuts import render
from django.http import JsonResponse
from django.db import transaction
from django.db.models import Prefetch, Q, F

from decimal import Decimal
from calendar import monthrange
import re
import csv
import json
from io import TextIOWrapper
from datetime import date, datetime, timedelta

from compte.models import Compte

from facture.models import Client, Fournisseur, Tr_detail, Source, Releve, CompteReleve
from facture.forms import TrDescForm, TrDetailFormSet
from facture.helpers.dates import verifier_exercice_modifiable
from facture.working_period import get_working_period
from facture.utils import get_setting, parse_decimal


def _detecter_compte_csv(row):
    """
    Détecte le no_compte, nom_institut et type_compte à partir d'une ligne CSV.
    Format banque  : col[0]=institution, col[1]=no_compte, col[2]=type_compte (ex: EOP)
    Format VISA    : col[0]=no_compte (contient 'VISA'), col[1] et col[2] vides
    Retourne (no_compte, nom_institut, type_compte)
    """
    col0 = row[0].strip() if len(row) > 0 else ''
    col1 = row[1].strip() if len(row) > 1 else ''
    col2 = row[2].strip() if len(row) > 2 else ''

    if col2:
        # Format banque : col2 contient le type de compte (ex: EOP)
        return col1, col0, col2
    else:
        # Format VISA / autre : col0 est l'identifiant du compte
        return col0, '', ''


def _obtenir_ou_creer_compte_releve(no_compte, nom_institut, type_compte_csv):
    """
    Trouve ou crée un CompteReleve. Infère le type_onglet depuis type_compte_csv.
    """
    no_compte_upper = no_compte.upper()
    if 'VISA' in no_compte_upper or 'CC' in no_compte_upper:
        type_onglet = 'carte_credit'
        # Extraire les 4 derniers chiffres : "VISA**** **** **** 5011" → "Visa 5011"
        chiffres = ''.join(filter(str.isdigit, no_compte))
        nom_affichage = f"Visa {chiffres[-4:]}" if len(chiffres) >= 4 else no_compte
    elif 'MC' in no_compte_upper or 'MARGE' in no_compte_upper:
        type_onglet = 'marge_credit'
        nom_affichage = no_compte
    elif type_compte_csv:
        type_onglet = 'banque'
        nom_affichage = f"{no_compte} {type_compte_csv}"
    else:
        type_onglet = 'autre'
        nom_affichage = no_compte

    default_compte_comptable = None

    # Héritage intelligent pour les nouveaux comptes similaires:
    # - cartes Visa: si un seul compte comptable est deja utilise pour les cartes Visa,
    #   on le reprend automatiquement sur la nouvelle carte.
    # - marge de credit: meme principe pour les comptes de marge.
    # - banque: seulement si no_compte + type_compte trouvent deja un mapping (rare).
    if type_onglet == 'carte_credit' and 'VISA' in no_compte_upper:
        visa_compte_ids = list(
            CompteReleve.objects.filter(
                type_onglet='carte_credit',
                no_compte__icontains='VISA',
                compte_comptable__isnull=False,
            ).values_list('compte_comptable_id', flat=True).distinct()
        )
        if len(visa_compte_ids) == 1:
            default_compte_comptable = Compte.objects.filter(pk=visa_compte_ids[0]).first()
    elif type_onglet == 'marge_credit':
        marge_compte_ids = list(
            CompteReleve.objects.filter(
                type_onglet='marge_credit',
                compte_comptable__isnull=False,
            ).values_list('compte_comptable_id', flat=True).distinct()
        )
        if len(marge_compte_ids) == 1:
            default_compte_comptable = Compte.objects.filter(pk=marge_compte_ids[0]).first()

    compte, _ = CompteReleve.objects.get_or_create(
        no_compte=no_compte,
        type_compte=type_compte_csv,
        defaults={
            'nom_affichage': nom_affichage,
            'nom_institut': nom_institut,
            'type_onglet': type_onglet,
            'compte_comptable': default_compte_comptable,
        },
    )
    return compte


def _relink_releves_compte_type_mismatch():
    """Reassocie les lignes Releve au bon CompteReleve quand type_compte differe."""
    mismatches = Releve.objects.select_related('compte_releve').exclude(
        compte_releve__isnull=True
    ).exclude(
        type_compte=F('compte_releve__type_compte')
    )

    for releve in mismatches:
        corrected_compte = _obtenir_ou_creer_compte_releve(
            releve.no_compte,
            releve.nom_institut,
            releve.type_compte,
        )
        if releve.compte_releve_id != corrected_compte.id:
            releve.compte_releve = corrected_compte
            releve.save(update_fields=['compte_releve'])


def _suggest_compte_from_releve(releve):
    if releve.compte_releve_id and getattr(releve.compte_releve, 'compte_comptable_id', None):
        return releve.compte_releve.compte_comptable

    numero_compte = ''.join(ch for ch in (releve.no_compte or '') if ch.isdigit())
    if not numero_compte:
        return None

    candidates = [numero_compte]
    if len(numero_compte) > 4:
        candidates.extend([numero_compte[:4], numero_compte[-4:]])

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            compte = Compte.objects.filter(numero=int(candidate)).first()
        except (TypeError, ValueError):
            compte = None
        if compte:
            return compte
    return None


def _suggest_montant_from_releve(releve):
    if releve.depot is not None and releve.depot != 0:
        return abs(releve.depot)
    if releve.retrait is not None and releve.retrait != 0:
        return -abs(releve.retrait)
    return None


def _compte_releve_aliases(compte_releve):
    aliases = set()
    if not compte_releve:
        return aliases

    raw_values = [
        compte_releve.nom_affichage or '',
        compte_releve.no_compte or '',
        compte_releve.type_compte or '',
    ]

    for raw in raw_values:
        value = str(raw).strip().upper()
        if not value:
            continue
        aliases.add(value)
        for token in re.findall(r'[A-Z0-9]+', value):
            if len(token) >= 3:
                aliases.add(token)

    numero = ''.join(ch for ch in (compte_releve.no_compte or '') if ch.isdigit())
    if numero:
        aliases.add(numero)
        if len(numero) >= 4:
            aliases.add(numero[-4:])

    return aliases


def _description_alias_score(description, aliases):
    if not description or not aliases:
        return 0
    desc = str(description).upper()
    score = 0
    for alias in aliases:
        if alias and alias in desc:
            score = max(score, len(alias))
    return score


def _find_releve_counterpart(current_releve, compte_cible, montant_cible):
    """Trouve une ligne de releve contrepartie (montant oppose, meme date) sur le compte cible."""
    if not current_releve or not compte_cible or montant_cible is None:
        return None

    comptes_releves_cibles = list(CompteReleve.objects.filter(compte_comptable=compte_cible))
    if not comptes_releves_cibles:
        return None

    depot_present = current_releve.depot is not None and current_releve.depot != 0
    retrait_present = current_releve.retrait is not None and current_releve.retrait != 0
    if depot_present == retrait_present:
        return None

    base_qs = Releve.objects.filter(
        compte_releve__in=comptes_releves_cibles,
        date=current_releve.date,
    ).exclude(pk=current_releve.pk).select_related('compte_releve')

    if depot_present:
        # Ligne courante: depot. Contrepartie attendue: retrait du meme montant.
        base_qs = base_qs.filter(Q(retrait=montant_cible) | Q(retrait=-montant_cible))
    else:
        # Ligne courante: retrait. Contrepartie attendue: depot du meme montant.
        base_qs = base_qs.filter(Q(depot=montant_cible) | Q(depot=-montant_cible))

    candidates = list(base_qs.order_by('ecriture_creee', 'id'))
    if not candidates:
        return None

    # Validation douce par indice textuel (ex: EOP, ET2, VISA 5011) pour reduire les faux positifs.
    source_aliases = _compte_releve_aliases(getattr(current_releve, 'compte_releve', None))
    target_aliases = set()
    for compte_releve in comptes_releves_cibles:
        target_aliases.update(_compte_releve_aliases(compte_releve))

    scored = []
    for candidate in candidates:
        score_from_current_desc = _description_alias_score(current_releve.desc_releve, target_aliases)
        score_from_candidate_desc = _description_alias_score(candidate.desc_releve, source_aliases)
        combined_score = max(score_from_current_desc, score_from_candidate_desc)
        scored.append((combined_score, 0 if not candidate.ecriture_creee else 1, candidate.id, candidate))

    scored.sort(key=lambda item: (-item[0], item[1], item[2]))

    # S'il y a un indice descriptif, on le privilegie.
    if scored[0][0] > 0:
        return scored[0][3]

    # Regle stricte: s'il y a plusieurs candidates mais aucun indice textuel,
    # on ne choisit pas automatiquement pour eviter les faux positifs.
    if len(scored) > 1:
        return None

    # Sans indice, conserver le comportement precedent (premiere non transmise puis plus ancienne).
    return scored[0][3]


def _normalize_desc_releve(text):
    """Normalise une description de relevé pour la comparaison de similarité."""
    return re.sub(r'\s+', ' ', (text or '').strip().upper())


def _tr_desc_detail_rows_hors_compte_lie(tr_desc, compte_lie_id, montant_compte_lie):
    """Retourne les lignes Tr_detail d'une écriture, en excluant la ligne du compte lié
    (compte de banque/carte lui-même) quand elle est identifiable."""
    detail_rows = []
    ligne_compte_lie_ignoree = False
    for detail in tr_desc.details.all():
        if (
            not ligne_compte_lie_ignoree
            and compte_lie_id
            and montant_compte_lie is not None
            and detail.compte_id == compte_lie_id
            and detail.montant == montant_compte_lie
        ):
            ligne_compte_lie_ignoree = True
            continue
        detail_rows.append({
            'compte_id': detail.compte_id,
            'compte_label': str(detail.compte),
            'montant': str(abs(detail.montant or Decimal('0'))),
        })
    return detail_rows


def _date_moins_mois(value, months):
    target_month_index = value.year * 12 + value.month - 1 - months
    target_year, target_month_index = divmod(target_month_index, 12)
    target_month = target_month_index + 1
    target_day = min(value.day, monthrange(target_year, target_month)[1])
    return date(target_year, target_month, target_day)


def _dernier_jour_mois_precedent(value):
    """Retourne le dernier jour du mois qui précède celui de `value`, afin d'exclure
    le mois en cours de la recherche d'écritures similaires."""
    return date(value.year, value.month, 1) - timedelta(days=1)


def _candidats_ecriture_similaire(releve=None, max_candidats=500):
    """Precalcule une fois (par requete) la liste des lignes de relevé qui ont deja une
    ecriture, avec leurs lignes de detail hors compte lie. Les virements inter-relevés
    restent des modèles valides: leur contrepartie est reliée à la même écriture lors
    de la création, ce qui évite de comptabiliser le virement deux fois."""
    candidats_qs = Releve.objects.filter(
        ecriture_creee=True,
        ecriture_tr_desc__isnull=False,
    )
    if releve and releve.date:
        date_maximum = _dernier_jour_mois_precedent(releve.date)
        candidats_qs = candidats_qs.filter(
            date__gte=_date_moins_mois(releve.date, 18),
            date__lte=date_maximum,
        )

    candidats_qs = candidats_qs.select_related(
        'ecriture_tr_desc',
        'ecriture_tr_desc__client',
        'ecriture_tr_desc__fournisseur',
        'compte_releve',
        'compte_releve__compte_comptable',
    ).prefetch_related(
        Prefetch('ecriture_tr_desc__details', queryset=Tr_detail.objects.select_related('compte').order_by('id')),
    ).order_by('-date', '-id')[:max_candidats]

    enrichis = []
    for candidat in candidats_qs:
        tr_desc = candidat.ecriture_tr_desc
        if not tr_desc:
            continue

        compte_lie_id = None
        if candidat.compte_releve_id and candidat.compte_releve and candidat.compte_releve.compte_comptable_id:
            compte_lie_id = candidat.compte_releve.compte_comptable_id

        montant_compte_lie = None
        if candidat.depot is not None and candidat.depot != 0 and not (candidat.retrait is not None and candidat.retrait != 0):
            montant_compte_lie = abs(candidat.depot)
        elif candidat.retrait is not None and candidat.retrait != 0 and not (candidat.depot is not None and candidat.depot != 0):
            montant_compte_lie = -abs(candidat.retrait)

        details = _tr_desc_detail_rows_hors_compte_lie(tr_desc, compte_lie_id, montant_compte_lie)

        enrichis.append({
            'releve_pk': candidat.pk,
            'desc_releve_normalisee': _normalize_desc_releve(candidat.desc_releve),
            'releve_id': candidat.pk,
            'desc_releve': candidat.desc_releve,
            'date_releve': candidat.date.strftime('%Y-%m-%d') if candidat.date else '',
            'no_ej': tr_desc.no_ej,
            'desc_ctb': tr_desc.desc_ctb or '',
            'compagnie_id': (f"client:{tr_desc.client_id}" if tr_desc.client_id else (f"fournisseur:{tr_desc.fournisseur_id}" if tr_desc.fournisseur_id else '')),
            'compagnie_label': str(tr_desc.client or tr_desc.fournisseur or ''),
            'details': details,
        })
    return enrichis


def _meilleures_correspondances(releve, candidats_enrichis, limit=1):
    """Retourne les écritures dont la description normalisée est strictement identique."""
    if not releve or not releve.date or not releve.desc_releve or releve.ecriture_creee:
        return []

    cible = _normalize_desc_releve(releve.desc_releve)
    date_minimum = _date_moins_mois(releve.date, 18).isoformat()
    date_maximum = _dernier_jour_mois_precedent(releve.date).isoformat()
    correspondances = []
    for candidat in candidats_enrichis:
        if candidat['releve_pk'] == releve.pk:
            continue
        if not date_minimum <= candidat['date_releve'] <= date_maximum:
            continue
        if cible == candidat['desc_releve_normalisee']:
            correspondances.append(candidat)

    correspondances.sort(key=lambda candidat: -candidat['releve_pk'])
    return correspondances[:limit]


def releve_ecriture_similaire(request, releve_id):
    """Vue AJAX: retourne, pour une ligne de relevé donnée, les écritures déjà créées
    sur des lignes de relevé dont la description est semblable."""
    releve = Releve.objects.select_related('compte_releve').filter(pk=releve_id).first()
    if not releve:
        return JsonResponse({'error': "Ligne de relevé introuvable."}, status=404)

    candidats = _candidats_ecriture_similaire(releve)
    resultats = _meilleures_correspondances(releve, candidats, limit=1)
    return JsonResponse({'resultats': resultats})


def _import_releve_csv(csv_file):
    errors = []

    file_name = csv_file.name
    if Releve.objects.filter(fichier_source=file_name).exists():
        errors.append(f"⚠ Le fichier « {file_name} » a déjà été importé. Aucune ligne n'a été ajoutée.")
        return errors

    raw_data = csv_file.file.read(5000)
    csv_file.file.seek(0)
    # Les releves Desjardins sont soit en ASCII pur, soit en Windows-1252
    # (jamais autre chose). On essaie utf-8 d'abord (au cas ou), puis on se
    # rabat sur cp1252 qui est le format reel utilise par Desjardins.
    # On evite chardet.detect() qui devine parfois a tort du Windows-1250
    # (Europe centrale), ce qui corrompt les caracteres accentues
    # (ex: 'e' devient 'c caron').
    encoding = 'cp1252'
    for candidate in ('utf-8', 'cp1252'):
        try:
            raw_data.decode(candidate)
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue

    text_file = TextIOWrapper(csv_file.file, encoding=encoding)
    sample = text_file.read(1024)
    text_file.seek(0)

    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(text_file, dialect=dialect)
    releves = []
    compte_releve_cache = {}

    for row_num, row in enumerate(reader, 1):
        try:
            if not row or all(not cell.strip() for cell in row):
                continue

            if len(row) < 12:
                errors.append(f"Ligne {row_num}: {len(row)} colonnes trouvées. Données: {row[:3]}")
                continue

            no_compte, nom_institut, type_compte = _detecter_compte_csv(row)
            date_str = row[3].strip() if len(row) > 3 else ''
            no_ligne = row[4].strip() if len(row) > 4 else ''
            desc_releve = row[5].strip() if len(row) > 5 else ''

            if not all([no_compte, date_str, no_ligne, desc_releve]):
                errors.append(f"Ligne {row_num}: Données manquantes")
                continue

            try:
                date_obj = datetime.strptime(date_str, '%Y/%m/%d').date()
            except ValueError:
                errors.append(f"Ligne {row_num}: Format de date invalide ({date_str})")
                continue

            if type_compte:
                no_cheque = row[6].strip() if len(row) > 6 else ''
                retrait = parse_decimal(row[7] if len(row) > 7 else '', none_if_blank=True)
                depot = parse_decimal(row[8] if len(row) > 8 else '', none_if_blank=True)
                solde = parse_decimal(row[13] if len(row) > 13 else '', none_if_blank=False) or Decimal('0')
            else:
                no_cheque = ''
                charge = parse_decimal(row[11] if len(row) > 11 else '', none_if_blank=True)
                paiement = parse_decimal(row[12] if len(row) > 12 else '', none_if_blank=True)
                retrait = charge if charge and charge > 0 else None
                depot = abs(paiement) if paiement and paiement < 0 else None
                solde = Decimal('0')

            cache_key = (no_compte, type_compte)
            if cache_key not in compte_releve_cache:
                compte_releve_cache[cache_key] = _obtenir_ou_creer_compte_releve(
                    no_compte, nom_institut, type_compte
                )

            releve_data = {
                'compte_releve': compte_releve_cache[cache_key],
                'fichier_source': file_name,
                'nom_institut': nom_institut,
                'no_compte': no_compte,
                'type_compte': type_compte,
                'date': date_obj,
                'no_ligne': no_ligne,
                'desc_releve': desc_releve,
                'desc_ctb': desc_releve[:40],
                'no_cheque': no_cheque,
                'retrait': retrait,
                'depot': depot,
                'solde': solde,
                'ecriture_creee': False,
            }

            releves.append(releve_data)

        except Exception as exc:
            errors.append(f"Ligne {row_num}: Erreur lors du parsing ({str(exc)})")
            continue

    if errors:
        errors.insert(0, "Le fichier est invalide. Aucune ligne n'a été ajoutée.")
    elif releves:
        try:
            with transaction.atomic():
                for data in releves:
                    Releve.objects.create(**data)
            errors.insert(0, f"✓ {len(releves)} ligne(s) ajoutée(s) à la base de données avec succès!")
        except Exception as exc:
            errors.append(f"Erreur lors de l'insertion: {str(exc)}")
    else:
        errors.append("Le fichier ne contient aucune ligne de relevé valide.")

    return errors


def releve_bancaire(request):
    releves = []
    errors = []
    open_releve_modal = False
    modal_releve_id = ''
    selected_compagnie_id = ''
    comptes_queryset = Compte.objects.all().order_by('numero')
    compagnies = sorted(
        [{'type': 'client', 'obj': c, 'key': f'client:{c.pk}'} for c in Client.objects.filter(active=True)] +
        [{'type': 'fournisseur', 'obj': f, 'key': f'fournisseur:{f.pk}'} for f in Fournisseur.objects.filter(active=True)],
        key=lambda item: item['obj'].nom.lower()
    )
    settings_instance = get_setting()
    company_required_account_ids = {
        account_id
        for account_id in (
            settings_instance.cap_id if settings_instance else None,
            settings_instance.car_id if settings_instance else None,
        )
        if account_id is not None
    }

    tr_desc_form = TrDescForm(prefix='trdesc_releve')
    tr_detail_formset = TrDetailFormSet(
        prefix='detail_releve',
        form_kwargs={'comptes_queryset': comptes_queryset}
    )

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()

        if action == 'create_ecriture':
            releve_id = (request.POST.get('releve_id') or '').strip()
            selected_compagnie_raw = (request.POST.get('compagnie_id') or '').strip()
            if ':' in selected_compagnie_raw:
                selected_compagnie_type, selected_compagnie_id = selected_compagnie_raw.split(':', 1)
                selected_compagnie_type = selected_compagnie_type.strip().lower()
            else:
                selected_compagnie_type = 'client'
                selected_compagnie_id = selected_compagnie_raw
            modal_releve_id = releve_id
            open_releve_modal = True
            releve = Releve.objects.select_related('ecriture_tr_desc', 'compte_releve', 'compte_releve__compte_comptable').filter(pk=releve_id).first()
            existing_tr_desc = releve.ecriture_tr_desc if releve and releve.ecriture_creee and releve.ecriture_tr_desc_id else None
            selected_compagnie_model = Fournisseur if selected_compagnie_type == 'fournisseur' else Client
            selected_compagnie = None
            if selected_compagnie_id:
                selected_compagnie = selected_compagnie_model.objects.filter(pk=selected_compagnie_id).first()
                if not selected_compagnie:
                    errors.append("Compagnie invalide.")

            tr_desc_form = TrDescForm(request.POST, prefix='trdesc_releve', instance=existing_tr_desc)
            tr_detail_formset = TrDetailFormSet(
                request.POST,
                prefix='detail_releve',
                form_kwargs={'comptes_queryset': comptes_queryset}
            )

            if not releve:
                errors.append("Ligne de relevé introuvable.")
            else:
                depot_present = releve.depot is not None and releve.depot != 0
                retrait_present = releve.retrait is not None and releve.retrait != 0

                if depot_present and retrait_present:
                    errors.append("La ligne de relevé contient dépôt et retrait en même temps; impossible de déterminer le sens.")
                elif not depot_present and not retrait_present:
                    errors.append("La ligne de relevé ne contient ni dépôt ni retrait.")

                compte_lie = None
                if releve.compte_releve_id and releve.compte_releve.compte_comptable_id:
                    compte_lie = releve.compte_releve.compte_comptable
                else:
                    compte_lie = _suggest_compte_from_releve(releve)

                if not compte_lie:
                    errors.append(
                        "Aucun compte de grand livre lié au compte de relevé. Configure `compte_comptable` sur ce compte de relevé."
                    )

                if not errors and tr_desc_form.is_valid() and tr_detail_formset.is_valid():
                    detail_rows = []
                    for detail_form in tr_detail_formset:
                        cleaned_data = detail_form.cleaned_data
                        if not cleaned_data:
                            continue
                        compte = cleaned_data.get('compte')
                        montant = cleaned_data.get('montant')
                        if compte and montant is not None:
                            detail_rows.append((compte, abs(montant)))

                    if not detail_rows:
                        errors.append("Ajoute au moins une ligne Tr_detail (compte + montant).")
                    else:
                        detail_compte_ids = [compte.pk for compte, _ in detail_rows if getattr(compte, 'pk', None) is not None]
                        compte_ids_releves = set(
                            CompteReleve.objects.filter(compte_comptable_id__in=detail_compte_ids)
                            .values_list('compte_comptable_id', flat=True)
                        )
                        is_virement_inter_releves = any(compte.pk in compte_ids_releves for compte, _ in detail_rows)
                        used_account_ids = {compte.pk for compte, _ in detail_rows}
                        if compte_lie:
                            used_account_ids.add(compte_lie.pk)
                        company_is_required = bool(used_account_ids & company_required_account_ids)
                        if company_is_required and selected_compagnie is None:
                            errors.append(
                                "Une compagnie est obligatoire lorsqu'une ligne utilise le compte CAP ou CAR."
                            )
                        compagnie_ecriture = selected_compagnie if company_is_required else (
                            None if is_virement_inter_releves else selected_compagnie
                        )

                        montant_releve = abs(releve.depot) if depot_present else abs(releve.retrait)
                        total_contrepartie = sum((montant for _, montant in detail_rows), Decimal('0'))
                        try:
                            verifier_exercice_modifiable(releve.date)
                            exercice_error = None
                        except ValueError as exc:
                            exercice_error = str(exc)

                        if exercice_error:
                            errors.append(exercice_error)
                            return_open = True
                        elif company_is_required and selected_compagnie is None:
                            return_open = True
                        elif total_contrepartie != montant_releve:
                            errors.append(
                                f"La somme des lignes Tr_detail ({total_contrepartie:.2f}) doit egaler le montant du relevé ({montant_releve:.2f})."
                            )
                            return_open = True
                        else:
                            return_open = False

                        if return_open:
                            pass
                        else:
                            # Sens comptable:
                            # - Depot => compte lie au debit (+), lignes modal au credit (-)
                            # - Retrait => compte lie au credit (-), lignes modal au debit (+)
                            montant_compte_lie = montant_releve if depot_present else -montant_releve
                            signe_contrepartie = Decimal('-1') if depot_present else Decimal('1')

                            with transaction.atomic():
                                source_nom = ''
                                if releve.compte_releve_id and releve.compte_releve and releve.compte_releve.nom_affichage:
                                    source_nom = releve.compte_releve.nom_affichage.strip()
                                if not source_nom:
                                    source_nom = f"{(releve.no_compte or '').strip()} {(releve.type_compte or '').strip()}".strip()
                                if not source_nom:
                                    source_nom = '0024883 EOP'

                                source_releve, _ = Source.objects.get_or_create(nom=source_nom[:15])
                                tr_desc = tr_desc_form.save(commit=False)
                                if not tr_desc.no_ej:
                                    tr_desc.no_ej = _next_no_ej(tr_desc.date)
                                tr_desc.desc_releve = tr_desc.desc_releve or releve.desc_releve or ''
                                tr_desc.source = source_releve
                                if selected_compagnie_type == 'fournisseur':
                                    tr_desc.fournisseur = compagnie_ecriture
                                    tr_desc.client = None
                                else:
                                    tr_desc.client = compagnie_ecriture
                                    tr_desc.fournisseur = None
                                tr_desc.save()

                                releve.desc_ctb = tr_desc.desc_ctb or releve.desc_releve

                                Tr_detail.objects.filter(tr_desc=tr_desc).delete()

                                Tr_detail.objects.create(
                                    tr_desc=tr_desc,
                                    compte=compte_lie,
                                    montant=montant_compte_lie,
                                )

                                for compte, montant in detail_rows:
                                    Tr_detail.objects.create(
                                        tr_desc=tr_desc,
                                        compte=compte,
                                        montant=signe_contrepartie * montant,
                                    )

                                if releve.compte_releve_id and not releve.compte_releve.compte_comptable_id:
                                    releve.compte_releve.compte_comptable = compte_lie
                                    releve.compte_releve.save(update_fields=['compte_comptable'])

                                releve.ecriture_creee = True
                                releve.ecriture_tr_desc = tr_desc
                                releve.save(update_fields=['desc_ctb', 'ecriture_creee', 'ecriture_tr_desc'])

                                lignes_liees = []
                                for compte, montant in detail_rows:
                                    counterpart = _find_releve_counterpart(releve, compte, montant)
                                    if not counterpart:
                                        continue
                                    if counterpart.ecriture_tr_desc_id and counterpart.ecriture_tr_desc_id != tr_desc.id:
                                        continue

                                    counterpart.ecriture_creee = True
                                    counterpart.ecriture_tr_desc = tr_desc
                                    counterpart.save(update_fields=['ecriture_creee', 'ecriture_tr_desc'])
                                    lignes_liees.append(str(counterpart.no_ligne or counterpart.id))

                            if existing_tr_desc:
                                msg = f"✓ Écriture {tr_desc.no_ej} mise à jour pour la ligne #{releve.no_ligne}."
                            else:
                                msg = f"✓ Écriture {tr_desc.no_ej} créée pour la ligne #{releve.no_ligne}."
                            if is_virement_inter_releves:
                                msg += " Virement inter-relevés: compagnie laissée vide."
                            if lignes_liees:
                                msg += f" Contrepartie reliée: ligne(s) {', '.join(lignes_liees)}."
                            errors.insert(0, msg)
                            open_releve_modal = False
                            modal_releve_id = ''
                            selected_compagnie_id = ''
                            tr_desc_form = TrDescForm(prefix='trdesc_releve')
                            tr_detail_formset = TrDetailFormSet(
                                prefix='detail_releve',
                                form_kwargs={'comptes_queryset': comptes_queryset}
                            )

        elif request.FILES.get('csv_file'):
            csv_file = request.FILES['csv_file']

            try:
                with transaction.atomic():
                    import_messages = _import_releve_csv(csv_file)
                    if any(not message.startswith("✓") for message in import_messages):
                        transaction.set_rollback(True)
                    errors.extend(import_messages)
            except Exception as e:
                errors.append(f"Erreur lors de la lecture du fichier: {str(e)}")

    _relink_releves_compte_type_mismatch()

    working_period = get_working_period(request)
    selected_periode = working_period['value']
    mois_selectionne = str(working_period['month'])
    annee_selectionnee = str(working_period['year'])
    periode_label = working_period['label']

    comptes_releves = CompteReleve.objects.order_by('type_onglet', 'nom_affichage')

    # Construire les données par compte pour l'affichage dans les onglets
    releves_qs = Releve.objects.select_related(
        'compte_releve',
        'compte_releve__compte_comptable',
        'ecriture_tr_desc',
        'ecriture_tr_desc__client',
        'ecriture_tr_desc__fournisseur',
    ).prefetch_related(
        Prefetch('ecriture_tr_desc__details', queryset=Tr_detail.objects.select_related('compte').order_by('id')),
    ).order_by('date', 'no_ligne')
    if annee_selectionnee.isdigit():
        releves_qs = releves_qs.filter(date__year=int(annee_selectionnee))
    if mois_selectionne.isdigit() and 1 <= int(mois_selectionne) <= 12:
        releves_qs = releves_qs.filter(date__month=int(mois_selectionne))

    compte_releve_ids_with_lines = set(
        releves_qs.values_list('compte_releve_id', flat=True).distinct()
    )
    unlinked_comptes_with_lines = [
        compte for compte in comptes_releves
        if compte.compte_comptable_id is None and compte.pk in compte_releve_ids_with_lines
    ]

    # Precalcule une seule fois (pas par ligne) les ecritures existantes utilisables comme
    # modele, pour la suggestion automatique "ecriture similaire" affichee dans le tableau.
    candidats_ecriture_similaire = _candidats_ecriture_similaire()

    releves_par_compte = {}
    for compte in comptes_releves:
        releves_list = list(releves_qs.filter(compte_releve=compte))

        for releve in releves_list:
            suggested_compte = _suggest_compte_from_releve(releve)
            suggested_montant = _suggest_montant_from_releve(releve)
            releve.suggested_compte_id = suggested_compte.pk if suggested_compte else ''
            releve.suggested_compte_label = str(suggested_compte) if suggested_compte else ''
            releve.compte_lie_id = (
                releve.compte_releve.compte_comptable_id
                if releve.compte_releve_id and releve.compte_releve
                else releve.suggested_compte_id
            )
            releve.suggested_montant = suggested_montant
            releve.ecriture_date = ''
            releve.ecriture_description = ''
            releve.ecriture_compagnie_id = ''
            releve.ecriture_details_json = '[]'
            releve.similaire_disponible = False
            releve.similaire_no_ej = ''
            releve.similaire_desc_ctb = ''
            releve.similaire_compagnie_id = ''
            releve.similaire_details_json = '[]'

            if not releve.ecriture_creee:
                meilleures = _meilleures_correspondances(releve, candidats_ecriture_similaire, limit=1)
                if meilleures:
                    meilleure = meilleures[0]
                    releve.similaire_disponible = True
                    releve.similaire_no_ej = meilleure['no_ej']
                    releve.similaire_desc_ctb = meilleure['desc_ctb']
                    releve.similaire_compagnie_id = meilleure['compagnie_id']
                    releve.similaire_details_json = json.dumps(meilleure['details'])

            tr_desc = releve.ecriture_tr_desc
            if tr_desc:
                releve.ecriture_date = tr_desc.date.strftime('%Y-%m-%d') if tr_desc.date else ''
                releve.ecriture_description = tr_desc.desc_ctb or ''
                if tr_desc.fournisseur_id:
                    releve.ecriture_compagnie_id = f"fournisseur:{tr_desc.fournisseur_id}"
                elif tr_desc.client_id:
                    releve.ecriture_compagnie_id = f"client:{tr_desc.client_id}"
                else:
                    releve.ecriture_compagnie_id = ''

                montant_releve = abs(releve.depot) if (releve.depot is not None and releve.depot != 0) else abs(releve.retrait) if (releve.retrait is not None and releve.retrait != 0) else None
                montant_compte_lie = None
                if montant_releve is not None:
                    if releve.depot is not None and releve.depot != 0 and not (releve.retrait is not None and releve.retrait != 0):
                        montant_compte_lie = montant_releve
                    elif releve.retrait is not None and releve.retrait != 0 and not (releve.depot is not None and releve.depot != 0):
                        montant_compte_lie = -montant_releve

                compte_lie_id = None
                if releve.compte_releve_id and releve.compte_releve and releve.compte_releve.compte_comptable_id:
                    compte_lie_id = releve.compte_releve.compte_comptable_id

                detail_rows = _tr_desc_detail_rows_hors_compte_lie(tr_desc, compte_lie_id, montant_compte_lie)
                releve.ecriture_details_json = json.dumps(detail_rows)
        
        # Calculer le solde cumulatif pour les cartes de crédit
        if compte.type_onglet in ['carte_credit', 'marge_credit']:
            solde_cumulatif = Decimal('0')
            for releve in releves_list:
                # Pour les cartes: solde = solde_precedent + depot - retrait
                if releve.depot:
                    solde_cumulatif += abs(releve.depot)
                if releve.retrait:
                    solde_cumulatif -= abs(releve.retrait)
                # On met à jour le solde de l'objet (pour l'affichage seulement)
                releve.solde = solde_cumulatif
        
        releves_par_compte[compte.pk] = releves_list

    # Fichiers source distincts par compte
    fichiers_par_compte = {
        compte.pk: list(
            releves_qs.filter(compte_releve=compte)
            .order_by('fichier_source')
            .values_list('fichier_source', flat=True)
            .distinct()
        )
        for compte in comptes_releves
    }

    # Grouper les comptes par type_onglet pour les 4 onglets fixes
    types_onglets = [
        ('banque',        'Banque'),
        ('carte_credit',  'Carte de crédit'),
        ('marge_credit',  'Marge de crédit'),
        ('autre',         'Autre'),
    ]
    groupes = [
        {
            'type_onglet': type_val,
            'label': label,
            'comptes': [c for c in comptes_releves if c.type_onglet == type_val],
        }
        for type_val, label in types_onglets
    ]

    response = render(request, "releves/index.html", {
        'title': "Relevé bancaire",
        'errors': errors,
        'unlinked_comptes_with_lines': unlinked_comptes_with_lines,
        'open_releve_modal': open_releve_modal,
        'modal_releve_id': modal_releve_id,
        'selected_compagnie_id': selected_compagnie_id,
        # 'compagnies': compagnies,
        'compte_cap_id': settings_instance.cap_id if settings_instance else '',
        'compte_car_id': settings_instance.car_id if settings_instance else '',
        'selected_periode': selected_periode,
        'mois_selectionne': mois_selectionne,
        'annee_selectionnee': annee_selectionnee,
        'periode_label': periode_label,
        'tr_desc_form': tr_desc_form,
        'tr_detail_formset': tr_detail_formset,
        'groupes': groupes,
        'releves_par_compte': releves_par_compte,
        'fichiers_par_compte': fichiers_par_compte,
    })

    return response


