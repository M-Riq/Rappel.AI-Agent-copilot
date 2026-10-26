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

AFFICHEURS = {"info": "info", "success": "success", "warning": "warning", "error": "error"}


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
    return logique.libelle_incident(identifiant, etat["rappel_incidents"], etat["rappel_scenarios"])


def afficher_resume(client: ClientApi, identifiant: str, incident: dict | None) -> None:
    st.subheader(libelle_incident(identifiant))
    libelle, couleur, icone = logique.style_incident(incident)
    st.badge(libelle, color=couleur, icon=icone)
    if incident:
        st.write(logique.resume_incident(incident))
        cle = f"rappel_decision:{client.url_base}:{identifiant}"
        decision = logique.libelle_decision(incident, st.session_state.get(cle))
        st.caption(f"Décision humaine : {decision}")
        resultat = incident.get("resultat_action")
        if resultat in ("reussi", "echoue"):
            st.caption("Résultat de l'action confirmé : " + ("réussi" if resultat == "reussi" else "échoué"))
        mention = logique.mention_simulation(incident)
        if mention:
            st.caption(mention)
    st.expander("Identifiant complet", icon=":material/tag:").text(identifiant)


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
    recherche = colonnes[0].text_input("Recherche", key=f"{cle}_recherche", placeholder="Rechercher dans cette vue") or ""
    filtres["recherche"] = recherche
    selections = {}
    for colonne, (champ, libelle) in zip(colonnes[1:], champs):
        options = sorted({str(ligne[champ]) for ligne in lignes if ligne.get(champ)})
        valeurs = st.session_state.get(f"{cle}_{champ}", filtres.get(champ, []))
        st.session_state[f"{cle}_{champ}"] = [valeur for valeur in valeurs if valeur in options]
        selections[champ] = colonne.multiselect(libelle, options, key=f"{cle}_{champ}", placeholder="Tous") or []
        filtres[champ] = selections[champ]
    st.button("Réinitialiser", icon=":material/filter_alt_off:", key=f"{cle}_effacer",
              on_click=reinitialiser_filtres, args=(cle, champs))
    resultat = logique.filtrer_lignes(lignes, recherche, selections)
    st.caption(f"{len(resultat)} / {len(lignes)} éléments")
    if not resultat:
        st.info("Aucun résultat pour ces filtres.")
    return resultat


def pagination_affichage(lignes: list[dict], cle: str) -> list[dict]:
    if not lignes:
        return []
    colonne_taille, colonne_page = st.columns(2)
    taille = colonne_taille.selectbox("Éléments par page", (10, 25, 50), key=f"{cle}_taille")
    _, numero, total = logique.paginer(lignes, st.session_state.get(f"{cle}_page", 1), taille)
    st.session_state[f"{cle}_page"] = numero
    numero = colonne_page.number_input("Page", min_value=1, max_value=total, step=1, key=f"{cle}_page") or 1
    return logique.paginer(lignes, int(numero), taille)[0]


def tableau_affichage(lignes: list[dict], cle: str, colonnes_defaut: list[str] | None = None) -> None:
    if not lignes:
        return
    colonnes = list(lignes[0])
    defaut = colonnes if colonnes_defaut is None else colonnes_defaut
    preferences = st.session_state.setdefault("rappel_tableaux", {}).setdefault(cle, {})
    st.session_state.setdefault(f"{cle}_tri", preferences.get("tri", "Ordre initial"))
    st.session_state.setdefault(f"{cle}_decroissant", preferences.get("decroissant", False))
    st.session_state.setdefault(f"{cle}_colonnes", preferences.get("colonnes", defaut))
    reglages = st.expander("Affichage du tableau", icon=":material/table_view:")
    tri = reglages.selectbox("Trier par", ["Ordre initial", *colonnes], key=f"{cle}_tri")
    decroissant = bool(reglages.checkbox("Ordre décroissant", key=f"{cle}_decroissant"))
    selection = reglages.multiselect("Colonnes visibles", colonnes, key=f"{cle}_colonnes")
    if selection is None:
        selection = defaut
    preferences.update(tri=tri, decroissant=decroissant, colonnes=selection)
    if not selection:
        st.info("Aucune colonne sélectionnée.")
        return
    triees = logique.trier_lignes(lignes, tri if tri in colonnes else None, decroissant)
    page = pagination_affichage(triees, cle)
    st.table([{colonne: ligne.get(colonne) for colonne in selection} for ligne in page])


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
        st.sidebar.selectbox("Incident sélectionné", incidents, key="rappel_choix_incident",
                             format_func=libelles.__getitem__, on_change=changer_incident)
        st.sidebar.caption(f"{len(incidents)} incident(s) dans cette session")
    else:
        st.sidebar.caption("Aucun incident dans cette session.")
    try:
        sante = client.sante()
    except ErreurApi:
        st.sidebar.warning("API indisponible")
        return
    if not isinstance(sante, dict) or sante.get("llm") not in ("fake", "meterless"):
        st.sidebar.warning("Mode API non confirmé")
        return
    st.sidebar.caption("Simulation" if sante.get("llm") == "fake" else "Mode réel")
    st.sidebar.caption(f"Source : {sante.get('source', 'inconnue')} · Mémoire : {sante.get('memoire', 'inconnue')}")


def afficher_evenement(evenement: dict, simulation: bool) -> None:
    gravite, libelle = logique.style_evenement(evenement)
    st.caption(f"Étape {evenement.get('etape', '?')} · {libelle} · {evenement.get('acteur', '')}")
    etiquette = logique.etiquette_debat(evenement)
    if etiquette:
        st.caption(etiquette)
    getattr(st, AFFICHEURS[gravite])(logique.texte_evenement(evenement))
    badge = logique.badge_modele(evenement, simulation)
    if badge:
        st.caption(badge)
    requete = logique.sql_a_afficher(evenement)
    if requete:
        st.expander(f"Requête SQL · étape {evenement.get('etape', '?')}", icon=":material/code:").code(
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
        st.warning("État de l'incident non confirmé : nouvelle injection suspendue.")
        return False
    etat.setdefault("rappel_resultats", {})[identifiant] = incident
    if not logique.enquete_terminee(incident):
        st.caption("Enquête en cours : nouvelle injection suspendue.")
        return False
    return True


def budget_enquetes(client: ClientApi) -> int | None:
    try:
        sante = client.sante()
    except ErreurApi as erreur:
        st.warning(str(erreur))
        return None
    if not isinstance(sante, dict) or sante.get("llm") not in ("fake", "meterless"):
        st.warning("Mode du modèle non confirmé : lancement suspendu.")
        return None
    simulation = sante["llm"] == "fake"
    accord = False
    if simulation:
        st.caption("Mode simulé : aucun appel LLM facturé.")
    else:
        st.warning("Mode réel : les enquêtes peuvent entraîner des appels LLM facturés.")
        etat = _etat()
        etat.setdefault("rappel_accord_reel", etat.get("rappel_accord_session", False))
        accord = st.checkbox("Autoriser jusqu'à 9 enquêtes réelles dans cette session", key="rappel_accord_reel")
        etat["rappel_accord_session"] = bool(accord)
    return logique.plafond_enquetes(simulation, accord)


def zone_pipeline(client: ClientApi) -> None:
    st.subheader("Nouvelle enquête")
    from rappel.pipeline_demo import NOMS_SCENARIOS, injecter, lire_etat

    etat = _etat()
    maximum = budget_enquetes(client)
    if maximum is None:
        return
    try:
        pipeline = lire_etat()
        st.caption(f"Pipeline de démo (simulé) : {pipeline.get('statut', 'inconnu')}")
    except (OSError, ValueError, KeyError):
        st.caption("Pipeline de démo absent : il sera créé à la première injection.")
    scenario = st.selectbox("Scénario", NOMS_SCENARIOS, key="rappel_scenario")
    disponible = injection_disponible(client)
    restants = max(0, maximum - etat["rappel_rejeux"])
    st.caption(f"Lancements : {etat['rappel_rejeux']} / {maximum}. Mémoire conservée entre les parcours.")
    if st.button(f"Injecter la panne et enquêter ({restants} essai(s) restant(s))", key="rappel_injecter",
                 disabled=not disponible or not logique.peut_rejouer(etat["rappel_rejeux"], maximum)):
        etat["rappel_rejeux"] += 1
        identifiant = logique.identifiant_incident(scenario, etat["rappel_rejeux"], etat["rappel_session"])
        try:
            injecter(scenario)
            etat["rappel_courant"] = identifiant
            client.creer_depuis_demo(identifiant)
        except ErreurApi as erreur:
            st.error(str(erreur))
            return
        except (OSError, ValueError):
            st.error("Injection impossible dans le pipeline de démo.")
            return
        etat["rappel_incidents"].append(identifiant)
        etat["rappel_evenements"][identifiant] = []
        etat["rappel_scenarios"][identifiant] = scenario
        etat["rappel_selection"] = identifiant
        st.rerun()
    if not logique.peut_rejouer(etat["rappel_rejeux"], maximum):
        st.info(f"Plafond de {maximum} enquêtes par session atteint (limite les appels au modèle).")


def zone_incidents(client: ClientApi) -> None:
    zone_pipeline(client)
    st.divider()
    st.subheader("Historique de cette session")
    etat = _etat()
    if not etat["rappel_incidents"]:
        st.info("Aucun incident dans cette session.")
        return
    st.button("Actualiser", icon=":material/refresh:", key="rappel_actualiser_incidents")
    st.caption("Derniers états reçus dans cette session.")
    champ_scenario = "scénario"
    lignes = []
    for identifiant in reversed(etat["rappel_incidents"]):
        incident = etat.get("rappel_resultats", {}).get(identifiant, {})
        lignes.append({
            "incident": libelle_incident(identifiant),
            "identifiant complet": identifiant,
            champ_scenario: etat["rappel_scenarios"].get(identifiant, "inconnu").upper(),
            "statut": logique.style_incident(incident)[0],
            "décision": logique.libelle_decision(incident),
        })
    lignes = filtres_affichage(lignes, "rappel_historique", ((champ_scenario, "Scénarios"), ("statut", "Statuts")))
    tableau_affichage(lignes, "rappel_historique", ["incident", champ_scenario, "statut", "décision"])


@st.fragment(run_every=logique.INTERVALLE_RAFRAICHISSEMENT_S)
def zone_fil(client: ClientApi) -> None:
    etat = _etat()
    identifiant = incident_selectionne()
    if not identifiant:
        st.caption("Aucune enquête lancée.")
        return
    try:
        evenements = synchroniser_evenements(client, identifiant)
        incident = client.lire_incident(identifiant)
    except ErreurApi as erreur:
        st.warning(logique.MESSAGE_API_COUPEE if erreur.statut is None else str(erreur))
        return
    etat.setdefault("rappel_resultats", {})[identifiant] = incident
    afficher_resume(client, identifiant, incident)
    if incident.get("correctif") and not (incident.get("decision_abstention") or {}).get("abstient"):
        st.page_link("pages/2_Actions.py", label="Examiner le correctif", icon=":material/task_alt:")
    st.subheader("Fil d'enquête")
    affiches = evenements
    bornes = logique.bornes_curseur(evenements)
    if logique.enquete_terminee(incident) and bornes:
        # Rejeu en lecture seule : le fil est affiché jusqu'à l'étape choisie, rien n'est recalculé.
        etape = st.slider("Étape (rejeu)", min_value=bornes[0], max_value=bornes[1], value=bornes[1],
                          key=f"rappel_etape_{identifiant}")
        affiches = logique.rejouer_jusqua(evenements, etape)
    affiches = filtres_affichage(affiches, "rappel_fil", (("type", "Types d'événement"), ("acteur", "Acteurs")))
    affiches = pagination_affichage(affiches, "rappel_fil")
    for evenement in affiches:
        afficher_evenement(evenement, bool(incident.get("enquete_simulee")))
    if not logique.enquete_terminee(incident):
        st.caption("Enquête en cours…")


def zone_memoire(client: ClientApi) -> None:
    st.subheader("Mémoire de l'incident")
    identifiant = incident_selectionne()
    if not identifiant:
        st.caption("Aucun incident sélectionné.")
        return
    st.write("Consultation pendant l'enquête")
    try:
        evenements = logique.evenements_memoire(synchroniser_evenements(client, identifiant))
    except ErreurApi as erreur:
        st.warning("Consultations indisponibles : " + str(erreur))
        return
    if not evenements:
        st.caption("Aucune consultation mémoire transmise pour cet incident.")
    for evenement in evenements:
        st.caption(f"Étape {evenement.get('etape', '?')}")
        st.write(logique.texte_evenement(evenement))
    st.caption("Identifiants des incidents consultés : non transmis par l'API.")
    try:
        graphe = client.graphe(identifiant)
        signature = logique.signature_incident(graphe, identifiant)
        if not signature:
            st.caption("Signature de l'incident non transmise.")
            return
        memoire = client.memoire(signature)
    except ErreurApi as erreur:
        st.warning("Mémoire indisponible : " + str(erreur))
        return
    st.write("Mémoire enregistrée pour cette signature")
    st.caption(signature)
    lignes = logique.lignes_souvenirs(memoire, signature)
    if lignes:
        lignes = filtres_affichage(lignes, "rappel_memoire", (("résultat", "Résultats confirmés"),))
        tableau_affichage(lignes, "rappel_memoire")
    else:
        st.caption("Aucun souvenir enregistré pour cette signature.")
    st.write("Hypothèses réfutées enregistrées")
    refutations = memoire["refutations_par_signature"].get(signature, [])
    if isinstance(refutations, list) and refutations:
        for texte in refutations:
            st.write(str(texte))
    else:
        st.caption("Aucune hypothèse réfutée enregistrée.")


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
        st.warning("Décision non confirmée. Vérifier son état côté API avant tout nouvel envoi.")
    elif decision_locale:
        st.info(f"Décision enregistrée : {decision_locale['decision']}.")
        action = decision_locale.get("action") or {}
        if action.get("statut"):
            st.write(f"État de l'action : {action['statut']}.")
        if decision_locale["execute"]:
            st.success("Correctif exécuté sur le pipeline de démonstration.")
        elif not action:
            st.info("Aucun patch exécuté.")
    elif incident.get("decision_humaine"):
        st.info(f"Décision enregistrée : {incident['decision_humaine']}.")
    resultat = incident.get("resultat_action")
    if resultat == "reussi":
        st.success("Résultat de l'action confirmé : réussi.")
    elif resultat == "echoue":
        st.error("Résultat de l'action confirmé : échoué.")
    elif incident.get("decision_humaine") and not decision_locale:
        st.caption("Résultat de l'action non confirmé.")


def afficher_action(client: ClientApi, identifiant: str, incident: dict) -> None:
    correctif = incident.get("correctif")
    if incident.get("statut") != "termine" or not correctif:
        return
    if (incident.get("decision_abstention") or {}).get("abstient"):
        return
    st.write(correctif.get("description", "Description non transmise."))
    st.metric("Niveau de risque", correctif.get("niveau_risque", "inconnu"))
    confiance = correctif.get("confiance")
    st.metric("Confiance", f"{confiance:.0%}" if isinstance(confiance, (int, float)) else "inconnue")
    st.write("Réversible : " + ("oui" if correctif.get("reversible") is True else "non confirmé"))
    st.write("Éléments touchés : " + ", ".join(correctif.get("elements_touches") or []))
    st.code(correctif.get("patch", ""), language="diff")
    st.write(correctif.get("justification", "Justification non transmise."))
    cle = f"rappel_decision:{client.url_base}:{identifiant}"
    etat = st.session_state
    verrouille = cle in etat or incident.get("decision_humaine") is not None
    applicable = correctif.get("niveau_risque") in ("auto", "validation") and correctif.get("reversible") is True
    colonne_a, colonne_b = st.columns(2)
    colonne_a.button(
        "Appliquer", icon=":material/check:", key=f"rappel_appliquer_{identifiant}",
        disabled=verrouille or not applicable, on_click=enregistrer_decision,
        args=(client, identifiant, "appliquer", cle),
    )
    colonne_b.button(
        "Refuser", icon=":material/close:", key=f"rappel_refuser_{identifiant}",
        disabled=verrouille, on_click=enregistrer_decision,
        args=(client, identifiant, "refuser", cle),
    )
    afficher_resultat_action(incident, etat.get(cle))


def zone_resultat(client: ClientApi, vue: str = "actions") -> None:
    etat = _etat()
    identifiant = incident_selectionne()
    incident = etat.get("rappel_resultats", {}).get(identifiant)
    st.button("Rafraîchir le résultat", icon=":material/refresh:", key="rappel_rafraichir")
    if identifiant:
        try:
            incident = client.lire_incident(identifiant)
            if vue == "compteurs":
                synchroniser_evenements(client, identifiant)
        except ErreurApi as erreur:
            st.warning(str(erreur))
            return
        etat.setdefault("rappel_resultats", {})[identifiant] = incident
        afficher_resume(client, identifiant, incident)
    else:
        st.info("Aucun incident sélectionné.")
    st.subheader("Décision humaine" if vue == "actions" else "Mesures de l'incident")
    if vue == "actions":
        if incident:
            afficher_action(client, identifiant, incident)
            st.page_link("pages/3_Memoire.py", label="Consulter la mémoire", icon=":material/history:")
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
    st.subheader("Comparaison P1 / P2")
    libelles = {identifiant: libelle_incident(identifiant) for identifiant in incidents}
    reference = st.selectbox("Incident de référence (P1)" if scenarios else "Incident de référence", references,
                             index=0, key="rappel_cmp_a", format_func=libelles.__getitem__)
    compare = st.selectbox("Incident comparé (P2)" if scenarios else "Incident comparé", compares,
                           index=len(compares) - 1, key="rappel_cmp_b", format_func=libelles.__getitem__)
    if reference == compare:
        return
    try:
        comparaison = client.comparaison(reference, compare)
    except ErreurApi as erreur:
        st.caption(str(erreur))
        return
    lignes = filtres_affichage(logique.lignes_compteur(comparaison), "rappel_comparaison", (("mesure", "Mesures comparées"),))
    tableau_affichage(lignes, "rappel_comparaison")
    st.write(logique.verdict_regle_60pc(comparaison))
    st.caption(comparaison.get("note", ""))


def zone_relations(client: ClientApi) -> None:
    st.subheader("Graphe de relations")
    identifiant = incident_selectionne()
    if not identifiant:
        st.caption("Toutes les relations enregistrées en mémoire.")
    try:
        graphe = client.graphe(identifiant)
    except ErreurApi as erreur:
        if erreur.statut == 404 and identifiant:
            graphe = {"noeuds": [], "aretes": [], "mermaid": ""}
        else:
            st.caption(logique.MESSAGE_API_COUPEE if erreur.statut is None else str(erreur))
            return
    if not graphe.get("noeuds"):
        st.caption("Aucune relation pour l'instant (mémoire vide).")
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
        st.graphviz_chart(logique.dot_relations(graphe))
    else:
        st.code(logique.dot_relations(graphe), language="dot")
    st.table(logique.tableau_relations(graphe))


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
    st.title(titre)
    identifiant = incident_selectionne()
    try:
        configuration = charger()
    except (ValueError, TypeError):
        st.error("Configuration invalide. Vérifiez les paramètres de l'application.")
        return
    client = ClientApi(configuration.api_url)
    if vue == "enquete":
        afficher(client)
        return
    with st.spinner("Chargement de la vue..."):
        if identifiant and vue in ("incidents", "memoire", "relations"):
            try:
                incident = client.lire_incident(identifiant)
                _etat().setdefault("rappel_resultats", {})[identifiant] = incident
            except ErreurApi:
                incident = None
                st.warning("Résumé indisponible : état de l'incident non confirmé.")
            afficher_resume(client, identifiant, incident)
        afficher(client)
