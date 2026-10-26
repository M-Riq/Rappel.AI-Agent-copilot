"""Vues de travail Streamlit partageant la sélection et l'état des incidents."""

from pathlib import Path
import sys
from uuid import uuid4

RACINE_UI = Path(__file__).resolve().parent
for chemin in (RACINE_UI, RACINE_UI.parent / "src"):
    if str(chemin) not in sys.path:
        sys.path.insert(0, str(chemin))

import streamlit as st

import logique
from client_api import ClientApi, ErreurApi
from ui.traductions import traduire

AFFICHEURS = {"info": "info", "success": "success", "warning": "warning", "error": "error"}
CHAMP_DECISION = "décision"
ORDRE_INITIAL = "Ordre initial"


def langue_interface() -> str:
    return "en" if st.session_state.get("rappel_langue") == "en" else "fr"


def texte(message: str, **valeurs) -> str:
    return traduire(message, langue_interface(), **valeurs)


def valeur_affichee(champ: str, valeur):
    champs_traduits = {"type", "acteur", "statut", CHAMP_DECISION, "mesure", "résultat", "relation",
                       "valeur", "a", "b", "reduction"}
    return texte(valeur) if champ in champs_traduits and isinstance(valeur, str) else valeur


def message_erreur(erreur: ErreurApi) -> str:
    if erreur.statut is not None and langue_interface() == "en":
        return texte("Erreur API {statut} : {detail}", statut=erreur.statut,
                     detail=erreur.detail if erreur.detail is not None else texte("requête refusée"))
    return texte(str(erreur))


def _etat():
    etat = st.session_state
    etat.setdefault("rappel_incidents", [])
    etat.setdefault("rappel_evenements", {})
    etat.setdefault("rappel_rejeux", 0)
    etat.setdefault("rappel_session", uuid4().hex)
    etat.setdefault("rappel_scenarios", {})
    return etat


def incident_selectionne() -> str | None:
    etat = _etat()
    return etat.get("rappel_selection") or etat.get("rappel_courant")


def changer_incident() -> None:
    st.session_state["rappel_selection"] = st.session_state["rappel_choix_incident"]


def libelle_incident(identifiant: str) -> str:
    etat = _etat()
    return logique.libelle_incident(identifiant, etat["rappel_incidents"], etat["rappel_scenarios"], langue_interface())


def afficher_resume(client: ClientApi, identifiant: str, incident: dict | None) -> None:
    st.subheader(libelle_incident(identifiant))
    libelle, couleur, icone = logique.style_incident(incident)
    st.badge(texte(libelle), color=couleur, icon=icone)
    if incident:
        st.write(logique.resume_incident(incident, langue_interface()))
        cle = f"rappel_decision:{client.url_base}:{identifiant}"
        decision = logique.libelle_decision(incident, st.session_state.get(cle))
        st.caption(texte("Décision humaine : {decision}", decision=texte(decision)))
        resultat = incident.get("resultat_action")
        if resultat in ("reussi", "echoue"):
            st.caption(texte("Résultat de l'action confirmé : {resultat}",
                             resultat=texte("réussi" if resultat == "reussi" else "échoué")))
        mention = logique.mention_simulation(incident, langue_interface())
        if mention:
            st.caption(mention)
    st.expander(texte("Identifiant complet"), icon=":material/tag:").text(identifiant)


def reinitialiser_filtres(cle: str, champs: tuple) -> None:
    st.session_state.setdefault("rappel_filtres", {}).pop(cle, None)
    st.session_state[f"{cle}_recherche"] = ""
    for champ, _ in champs:
        st.session_state[f"{cle}_{champ}"] = []
    st.session_state[f"{cle}_page"] = 1


def filtres_affichage(lignes: list[dict], cle: str, champs: tuple = ()) -> list[dict]:
    filtres = st.session_state.setdefault("rappel_filtres", {}).setdefault(cle, {})
    st.session_state.setdefault(f"{cle}_recherche", filtres.get("recherche", ""))
    colonnes = st.columns(len(champs) + 1)
    recherche = colonnes[0].text_input(texte("Recherche"), key=f"{cle}_recherche",
                                      placeholder=texte("Rechercher dans cette vue")) or ""
    filtres["recherche"] = recherche
    selections = {}
    for colonne, (champ, libelle) in zip(colonnes[1:], champs):
        options = sorted({str(ligne[champ]) for ligne in lignes if ligne.get(champ)})
        valeurs = st.session_state.get(f"{cle}_{champ}", filtres.get(champ, []))
        st.session_state[f"{cle}_{champ}"] = [valeur for valeur in valeurs if valeur in options]
        libelles = {option: valeur_affichee(champ, option) for option in options}
        selections[champ] = colonne.multiselect(texte(libelle), options, key=f"{cle}_{champ}",
                                                placeholder=texte("Tous"), format_func=libelles.__getitem__) or []
        filtres[champ] = selections[champ]
    st.button(texte("Réinitialiser"), icon=":material/filter_alt_off:", key=f"{cle}_effacer",
              on_click=reinitialiser_filtres, args=(cle, champs))
    resultat = [ligne for ligne in logique.filtrer_lignes(lignes, selections=selections)
                if logique.filtrer_lignes([
                    {champ: f"{valeur} {valeur_affichee(champ, valeur)}" for champ, valeur in ligne.items()}
                ], recherche)]
    st.caption(texte("{nombre} / {total} éléments", nombre=len(resultat), total=len(lignes)))
    if not resultat:
        st.info(texte("Aucun résultat pour ces filtres."))
    return resultat


def pagination_affichage(lignes: list[dict], cle: str) -> list[dict]:
    if not lignes:
        return []
    colonne_taille, colonne_page = st.columns(2)
    taille = colonne_taille.selectbox(texte("Éléments par page"), (10, 25, 50), key=f"{cle}_taille")
    _, numero, total = logique.paginer(lignes, st.session_state.get(f"{cle}_page", 1), taille)
    st.session_state[f"{cle}_page"] = numero
    numero = colonne_page.number_input(texte("Page"), min_value=1, max_value=total, step=1, key=f"{cle}_page") or 1
    return logique.paginer(lignes, int(numero), taille)[0]


def tableau_affichage(lignes: list[dict], cle: str, colonnes_defaut: list[str] | None = None) -> None:
    if not lignes:
        return
    colonnes = list(lignes[0])
    defaut = colonnes if colonnes_defaut is None else colonnes_defaut
    preferences = st.session_state.setdefault("rappel_tableaux", {}).setdefault(cle, {})
    st.session_state.setdefault(f"{cle}_tri", preferences.get("tri", ORDRE_INITIAL))
    st.session_state.setdefault(f"{cle}_decroissant", preferences.get("decroissant", False))
    st.session_state.setdefault(f"{cle}_colonnes", preferences.get("colonnes", defaut))
    libelles = {colonne: texte(colonne) for colonne in [ORDRE_INITIAL, *colonnes]}
    reglages = st.expander(texte("Affichage du tableau"), icon=":material/table_view:")
    tri = reglages.selectbox(texte("Trier par"), [ORDRE_INITIAL, *colonnes], key=f"{cle}_tri",
                             format_func=libelles.__getitem__)
    decroissant = bool(reglages.checkbox(texte("Ordre décroissant"), key=f"{cle}_decroissant"))
    selection = reglages.multiselect(texte("Colonnes visibles"), colonnes, key=f"{cle}_colonnes",
                                     format_func=libelles.__getitem__)
    if selection is None:
        selection = defaut
    preferences.update(tri=tri, decroissant=decroissant, colonnes=selection)
    if not selection:
        st.info(texte("Aucune colonne sélectionnée."))
        return
    triees = logique.trier_lignes(lignes, tri if tri in colonnes else None, decroissant)
    page = pagination_affichage(triees, cle)
    st.table([{libelles[colonne]: valeur_affichee(colonne, ligne.get(colonne)) for colonne in selection} for ligne in page])


def synchroniser_evenements(client: ClientApi, identifiant: str) -> list[dict]:
    etat = _etat()
    existants = etat["rappel_evenements"].get(identifiant, [])
    nouveaux = client.fil(identifiant, logique.derniere_etape(existants))
    evenements = logique.fusionner(existants, nouveaux)
    etat["rappel_evenements"][identifiant] = evenements
    return evenements


def barre_laterale(client: ClientApi) -> None:
    etat = _etat()
    incidents = etat["rappel_incidents"]
    if incidents:
        selection = incident_selectionne()
        etat["rappel_choix_incident"] = selection if selection in incidents else incidents[-1]
        libelles = {identifiant: libelle_incident(identifiant) for identifiant in incidents}
        st.sidebar.selectbox(texte("Incident sélectionné"), incidents, key="rappel_choix_incident",
                             format_func=libelles.__getitem__, on_change=changer_incident)
        st.sidebar.caption(texte("{nombre} incident(s) dans cette session", nombre=len(incidents)))
    else:
        st.sidebar.caption(texte("Aucun incident dans cette session."))
    try:
        sante = client.sante()
    except ErreurApi:
        st.sidebar.warning(texte("API indisponible"))
        return
    if not isinstance(sante, dict) or sante.get("llm") not in ("fake", "meterless"):
        st.sidebar.warning(texte("Mode API non confirmé"))
        return
    st.sidebar.caption(texte("Simulation" if sante.get("llm") == "fake" else "Mode réel"))
    st.sidebar.caption(texte("Source : {source} · Mémoire : {memoire}",
                             source=sante.get("source", texte("inconnue")),
                             memoire=sante.get("memoire", texte("inconnue"))))


def afficher_evenement(evenement: dict, simulation: bool) -> None:
    gravite, libelle = logique.style_evenement(evenement)
    st.caption(texte("Étape {etape} · {libelle} · {acteur}", etape=evenement.get("etape", "?"),
                     libelle=texte(libelle), acteur=texte(evenement.get("acteur", ""))))
    etiquette = logique.etiquette_debat(evenement, langue_interface())
    if etiquette:
        st.caption(etiquette)
    getattr(st, AFFICHEURS[gravite])(logique.texte_evenement(evenement, langue_interface()))
    badge = logique.badge_modele(evenement, simulation, langue_interface())
    if badge:
        st.caption(badge)
    requete = logique.sql_a_afficher(evenement)
    if requete:
        st.expander(texte("Requête SQL · étape {etape}", etape=evenement.get("etape", "?")), icon=":material/code:").code(
            requete, language="sql", wrap_lines=True,
        )


def injection_disponible(client: ClientApi) -> bool:
    etat = _etat()
    identifiant = etat.get("rappel_courant")
    if not identifiant:
        return True
    try:
        incident = client.lire_incident(identifiant)
    except ErreurApi:
        st.warning(texte("État de l'incident non confirmé : nouvelle injection suspendue."))
        return False
    etat.setdefault("rappel_resultats", {})[identifiant] = incident
    if not logique.enquete_terminee(incident):
        st.caption(texte("Enquête en cours : nouvelle injection suspendue."))
        return False
    return True


def budget_enquetes(client: ClientApi) -> int | None:
    try:
        sante = client.sante()
    except ErreurApi as erreur:
        st.warning(message_erreur(erreur))
        return None
    if not isinstance(sante, dict) or sante.get("llm") not in ("fake", "meterless"):
        st.warning(texte("Mode du modèle non confirmé : lancement suspendu."))
        return None
    simulation = sante["llm"] == "fake"
    accord = False
    if simulation:
        st.caption(texte("Mode simulé : aucun appel LLM facturé."))
    else:
        st.warning(texte("Mode réel : les enquêtes peuvent entraîner des appels LLM facturés."))
        etat = _etat()
        etat.setdefault("rappel_accord_reel", etat.get("rappel_accord_session", False))
        accord = st.checkbox(texte("Autoriser jusqu'à 9 enquêtes réelles dans cette session"), key="rappel_accord_reel")
        etat["rappel_accord_session"] = bool(accord)
    return logique.plafond_enquetes(simulation, accord)


def zone_pipeline(client: ClientApi) -> None:
    st.subheader(texte("Nouvelle enquête"))
    from rappel.pipeline_demo import NOMS_SCENARIOS, injecter, lire_etat

    etat = _etat()
    maximum = budget_enquetes(client)
    if maximum is None:
        return
    try:
        pipeline = lire_etat()
        st.caption(texte("Pipeline de démo (simulé) : {statut}", statut=texte(pipeline.get("statut", "inconnu"))))
    except (OSError, ValueError, KeyError):
        st.caption(texte("Pipeline de démo absent : il sera créé à la première injection."))
    scenario = st.selectbox(texte("Scénario"), NOMS_SCENARIOS, key="rappel_scenario")
    disponible = injection_disponible(client)
    restants = max(0, maximum - etat["rappel_rejeux"])
    st.caption(texte("Lancements : {nombre} / {maximum}. Mémoire conservée entre les parcours.",
                     nombre=etat["rappel_rejeux"], maximum=maximum))
    if st.button(texte("Injecter la panne et enquêter ({restants} essai(s) restant(s))", restants=restants), key="rappel_injecter",
                 disabled=not disponible or not logique.peut_rejouer(etat["rappel_rejeux"], maximum)):
        etat["rappel_rejeux"] += 1
        identifiant = logique.identifiant_incident(scenario, etat["rappel_rejeux"], etat["rappel_session"])
        try:
            injecter(scenario)
            etat["rappel_courant"] = identifiant
            client.creer_depuis_demo(identifiant)
        except ErreurApi as erreur:
            st.error(message_erreur(erreur))
            return
        except (OSError, ValueError):
            st.error(texte("Injection impossible dans le pipeline de démo."))
            return
        etat["rappel_incidents"].append(identifiant)
        etat["rappel_evenements"][identifiant] = []
        etat["rappel_scenarios"][identifiant] = scenario
        etat["rappel_selection"] = identifiant
        st.rerun()
    if not logique.peut_rejouer(etat["rappel_rejeux"], maximum):
        st.info(texte("Plafond de {maximum} enquêtes par session atteint (limite les appels au modèle).", maximum=maximum))


def zone_incidents(client: ClientApi) -> None:
    zone_pipeline(client)
    st.divider()
    st.subheader(texte("Historique de cette session"))
    etat = _etat()
    if not etat["rappel_incidents"]:
        st.info(texte("Aucun incident dans cette session."))
        return
    st.button(texte("Actualiser"), icon=":material/refresh:", key="rappel_actualiser_incidents")
    st.caption(texte("Derniers états reçus dans cette session."))
    champ_scenario = "scénario"
    lignes = []
    for identifiant in reversed(etat["rappel_incidents"]):
        incident = etat.get("rappel_resultats", {}).get(identifiant, {})
        lignes.append({
            "incident": libelle_incident(identifiant),
            "identifiant complet": identifiant,
            champ_scenario: etat["rappel_scenarios"].get(identifiant, "inconnu").upper(),
            "statut": logique.style_incident(incident)[0],
            CHAMP_DECISION: logique.libelle_decision(incident),
        })
    lignes = filtres_affichage(lignes, "rappel_historique", ((champ_scenario, "Scénarios"), ("statut", "Statuts")))
    tableau_affichage(lignes, "rappel_historique", ["incident", champ_scenario, "statut", CHAMP_DECISION])


@st.fragment(run_every=logique.INTERVALLE_RAFRAICHISSEMENT_S)
def zone_fil(client: ClientApi) -> None:
    etat = _etat()
    identifiant = incident_selectionne()
    if not identifiant:
        st.caption(texte("Aucune enquête lancée."))
        return
    try:
        evenements = synchroniser_evenements(client, identifiant)
        incident = client.lire_incident(identifiant)
    except ErreurApi as erreur:
        st.warning(texte(logique.MESSAGE_API_COUPEE) if erreur.statut is None else message_erreur(erreur))
        return
    etat.setdefault("rappel_resultats", {})[identifiant] = incident
    afficher_resume(client, identifiant, incident)
    if incident.get("correctif") and not (incident.get("decision_abstention") or {}).get("abstient"):
        st.page_link("pages/2_Actions.py", label=texte("Examiner le correctif"), icon=":material/task_alt:")
    st.subheader(texte("Fil d'enquête"))
    affiches = evenements
    bornes = logique.bornes_curseur(evenements)
    if logique.enquete_terminee(incident) and bornes:
        # Rejeu en lecture seule : le fil est affiché jusqu'à l'étape choisie, rien n'est recalculé.
        etape = st.slider(texte("Étape (rejeu)"), min_value=bornes[0], max_value=bornes[1], value=bornes[1],
                          key=f"rappel_etape_{identifiant}")
        affiches = logique.rejouer_jusqua(evenements, etape)
    affiches = filtres_affichage(affiches, "rappel_fil", (("type", "Types d'événement"), ("acteur", "Acteurs")))
    affiches = pagination_affichage(affiches, "rappel_fil")
    for evenement in affiches:
        afficher_evenement(evenement, bool(incident.get("enquete_simulee")))
    if not logique.enquete_terminee(incident):
        st.caption(texte("Enquête en cours…"))


def zone_memoire(client: ClientApi) -> None:
    st.subheader(texte("Mémoire de l'incident"))
    identifiant = incident_selectionne()
    if not identifiant:
        st.caption(texte("Aucun incident sélectionné."))
        return
    st.write(texte("Consultation pendant l'enquête"))
    try:
        evenements = logique.evenements_memoire(synchroniser_evenements(client, identifiant))
    except ErreurApi as erreur:
        st.warning(texte("Consultations indisponibles : {erreur}", erreur=message_erreur(erreur)))
        return
    if not evenements:
        st.caption(texte("Aucune consultation mémoire transmise pour cet incident."))
    for evenement in evenements:
        st.caption(texte("Étape {etape}", etape=evenement.get("etape", "?")))
        st.write(logique.texte_evenement(evenement, langue_interface()))
    st.caption(texte("Identifiants des incidents consultés : non transmis par l'API."))
    try:
        graphe = client.graphe(identifiant)
        signature = logique.signature_incident(graphe, identifiant)
        if not signature:
            st.caption(texte("Signature de l'incident non transmise."))
            return
        memoire = client.memoire(signature)
    except ErreurApi as erreur:
        st.warning(texte("Mémoire indisponible : {erreur}", erreur=message_erreur(erreur)))
        return
    st.write(texte("Mémoire enregistrée pour cette signature"))
    st.caption(signature)
    lignes = logique.lignes_souvenirs(memoire, signature)
    if lignes:
        lignes = filtres_affichage(lignes, "rappel_memoire", (("résultat", "Résultats confirmés"),))
        tableau_affichage(lignes, "rappel_memoire")
    else:
        st.caption(texte("Aucun souvenir enregistré pour cette signature."))
    st.write(texte("Hypothèses réfutées enregistrées"))
    refutations = memoire["refutations_par_signature"].get(signature, [])
    if isinstance(refutations, list) and refutations:
        for refutation in refutations:
            st.write(str(refutation))
    else:
        st.caption(texte("Aucune hypothèse réfutée enregistrée."))


def enregistrer_decision(client: ClientApi, identifiant: str, decision: str, cle: str) -> None:
    etat = st.session_state
    if cle in etat:
        return
    etat[cle] = {"confirmee": False, "decision": decision}
    try:
        reponse = client.decision(identifiant, decision)
        if (
            not isinstance(reponse, dict)
            or reponse.get("id") != identifiant
            or reponse.get("decision") != decision
            or type(reponse.get("execute")) is not bool
        ):
            return
        etat[cle] = {**reponse, "confirmee": True}
    except ErreurApi:
        return


def afficher_resultat_action(incident: dict, decision_locale: dict | None) -> None:
    if decision_locale and not decision_locale["confirmee"]:
        st.warning(texte("Décision non confirmée. Vérifier son état côté API avant tout nouvel envoi."))
    elif decision_locale:
        st.info(texte("Décision enregistrée : {decision}.", decision=texte(decision_locale["decision"])))
        action = decision_locale.get("action") or {}
        if action.get("statut"):
            st.write(texte("État de l'action : {statut}.", statut=texte(action["statut"])))
        if decision_locale["execute"]:
            st.success(texte("Correctif exécuté sur le pipeline de démonstration."))
        elif not action:
            st.info(texte("Aucun patch exécuté."))
    elif incident.get("decision_humaine"):
        st.info(texte("Décision enregistrée : {decision}.", decision=texte(incident["decision_humaine"])))
    resultat = incident.get("resultat_action")
    if resultat == "reussi":
        st.success(texte("Résultat de l'action confirmé : réussi."))
    elif resultat == "echoue":
        st.error(texte("Résultat de l'action confirmé : échoué."))
    elif incident.get("decision_humaine") and not decision_locale:
        st.caption(texte("Résultat de l'action non confirmé."))


def afficher_action(client: ClientApi, identifiant: str, incident: dict) -> None:
    correctif = incident.get("correctif")
    if incident.get("statut") != "termine" or not correctif:
        return
    if (incident.get("decision_abstention") or {}).get("abstient"):
        return
    st.write(correctif.get("description", texte("Description non transmise.")))
    st.metric(texte("Niveau de risque"), texte(correctif.get("niveau_risque", "inconnu")))
    confiance = correctif.get("confiance")
    st.metric(texte("Confiance"), f"{confiance:.0%}" if isinstance(confiance, (int, float)) else texte("inconnue"))
    st.write(texte("Réversible : {valeur}", valeur=texte("oui" if correctif.get("reversible") is True else "non confirmé")))
    st.write(texte("Éléments touchés : {elements}", elements=", ".join(correctif.get("elements_touches") or [])))
    st.code(correctif.get("patch", ""), language="diff")
    st.write(correctif.get("justification", texte("Justification non transmise.")))
    cle = f"rappel_decision:{client.url_base}:{identifiant}"
    etat = st.session_state
    verrouille = cle in etat or incident.get("decision_humaine") is not None
    applicable = correctif.get("niveau_risque") in ("auto", "validation") and correctif.get("reversible") is True
    colonne_a, colonne_b = st.columns(2)
    colonne_a.button(
        texte("Appliquer"), icon=":material/check:", key=f"rappel_appliquer_{identifiant}",
        disabled=verrouille or not applicable, on_click=enregistrer_decision,
        args=(client, identifiant, "appliquer", cle),
    )
    colonne_b.button(
        texte("Refuser"), icon=":material/close:", key=f"rappel_refuser_{identifiant}",
        disabled=verrouille, on_click=enregistrer_decision,
        args=(client, identifiant, "refuser", cle),
    )
    afficher_resultat_action(incident, etat.get(cle))


def zone_resultat(client: ClientApi, vue: str = "actions") -> None:
    etat = _etat()
    identifiant = incident_selectionne()
    incident = etat.get("rappel_resultats", {}).get(identifiant)
    st.button(texte("Rafraîchir le résultat"), icon=":material/refresh:", key="rappel_rafraichir")
    if identifiant:
        try:
            incident = client.lire_incident(identifiant)
            if vue == "compteurs":
                synchroniser_evenements(client, identifiant)
        except ErreurApi as erreur:
            st.warning(message_erreur(erreur))
            return
        etat.setdefault("rappel_resultats", {})[identifiant] = incident
        afficher_resume(client, identifiant, incident)
    else:
        st.info(texte("Aucun incident sélectionné."))
    st.subheader(texte("Décision humaine" if vue == "actions" else "Mesures de l'incident"))
    if vue == "actions":
        if incident:
            afficher_action(client, identifiant, incident)
            st.page_link("pages/3_Memoire.py", label=texte("Consulter la mémoire"), icon=":material/history:")
        return
    if incident:
        lignes = logique.lignes_metriques(incident, etat["rappel_evenements"].get(identifiant, []))
        lignes = filtres_affichage(lignes, "rappel_mesures", (("mesure", "Mesures"),))
        tableau_affichage(lignes, "rappel_mesures")
    afficher_comparaison(client)


def zone_compteurs(client: ClientApi) -> None:
    zone_resultat(client, "compteurs")


def afficher_comparaison(client: ClientApi) -> None:
    etat = _etat()
    incidents = etat["rappel_incidents"]
    scenarios = etat["rappel_scenarios"]
    references = [identifiant for identifiant in incidents if scenarios.get(identifiant) == "p1"] if scenarios else incidents
    compares = [identifiant for identifiant in incidents if scenarios.get(identifiant) == "p2"] if scenarios else incidents
    if len(incidents) < 2 or not references or not compares:
        return
    st.subheader(texte("Comparaison P1 / P2"))
    libelles = {identifiant: libelle_incident(identifiant) for identifiant in incidents}
    reference = st.selectbox(texte("Incident de référence (P1)" if scenarios else "Incident de référence"), references,
                             index=0, key="rappel_cmp_a", format_func=libelles.__getitem__)
    compare = st.selectbox(texte("Incident comparé (P2)" if scenarios else "Incident comparé"), compares,
                           index=len(compares) - 1, key="rappel_cmp_b", format_func=libelles.__getitem__)
    if reference == compare:
        return
    try:
        comparaison = client.comparaison(reference, compare)
    except ErreurApi as erreur:
        st.caption(message_erreur(erreur))
        return
    lignes = filtres_affichage(logique.lignes_compteur(comparaison), "rappel_comparaison", (("mesure", "Mesures comparées"),))
    tableau_affichage(lignes, "rappel_comparaison")
    st.write(logique.verdict_regle_60pc(comparaison, langue_interface()))
    st.caption(comparaison.get("note", ""))


def zone_relations(client: ClientApi) -> None:
    st.subheader(texte("Graphe de relations"))
    identifiant = incident_selectionne()
    if not identifiant:
        st.caption(texte("Toutes les relations enregistrées en mémoire."))
    try:
        graphe = client.graphe(identifiant)
    except ErreurApi as erreur:
        if erreur.statut == 404 and identifiant:
            graphe = {"noeuds": [], "aretes": [], "mermaid": ""}
        else:
            st.caption(texte(logique.MESSAGE_API_COUPEE) if erreur.statut is None else message_erreur(erreur))
            return
    if not graphe.get("noeuds"):
        st.caption(texte("Aucune relation pour l'instant (mémoire vide)."))
        return
    noeuds = {noeud["id"]: noeud for noeud in graphe["noeuds"]}
    relations = [{
        **arete,
        "relation": logique.LIBELLES_RELATIONS.get(arete["type"], arete["type"]),
        "libelle_source": noeuds.get(arete["source"], {}).get("libelle", ""),
        "libelle_cible": noeuds.get(arete["cible"], {}).get("libelle", ""),
    } for arete in graphe.get("aretes", [])]
    relations = filtres_affichage(relations, "rappel_relations", (("relation", "Relations"),))
    relations = pagination_affichage(relations, "rappel_relations")
    if not relations:
        return
    identifiants = {arete[champ] for arete in relations for champ in ("source", "cible")}
    graphe = {"noeuds": [noeud for noeud in graphe["noeuds"] if noeud["id"] in identifiants], "aretes": relations}
    if hasattr(st, "graphviz_chart"):
        st.graphviz_chart(logique.dot_relations(graphe, langue_interface()))
    else:
        st.code(logique.dot_relations(graphe, langue_interface()), language="dot")
    st.table([{texte(champ): valeur_affichee(champ, valeur) for champ, valeur in ligne.items()}
              for ligne in logique.tableau_relations(graphe, langue_interface())])


def afficher_page(vue: str) -> None:
    from rappel.config import charger

    vues = {
        "incidents": ("Incidents", zone_incidents),
        "enquete": ("Enquête", zone_fil),
        "actions": ("Actions", zone_resultat),
        "memoire": ("Mémoire", zone_memoire),
        "compteurs": ("Compteurs", zone_compteurs),
        "relations": ("Relations", zone_relations),
    }
    titre, afficher = vues[vue]
    st.title(texte(titre))
    identifiant = incident_selectionne()
    try:
        configuration = charger()
    except (ValueError, TypeError):
        st.error(texte("Configuration invalide. Vérifiez les paramètres de l'application."))
        return
    client = ClientApi(configuration.api_url)
    if vue == "enquete":
        afficher(client)
        return
    with st.spinner(texte("Chargement de la vue...")):
        if identifiant and vue in ("incidents", "memoire", "relations"):
            try:
                incident = client.lire_incident(identifiant)
                _etat().setdefault("rappel_resultats", {})[identifiant] = incident
            except ErreurApi:
                incident = None
                st.warning(texte("Résumé indisponible : état de l'incident non confirmé."))
            afficher_resume(client, identifiant, incident)
        afficher(client)
