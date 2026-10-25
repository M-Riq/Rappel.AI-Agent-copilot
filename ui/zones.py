"""Interface en trois zones : pipeline et incident, fil d'enquête en direct, résultat et compteur."""

from pathlib import Path
import sys

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
    return etat


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
        st.code(requete, language="sql")


def zone_pipeline(client: ClientApi) -> None:
    st.subheader("1. Pipeline et incident")
    from rappel.pipeline_demo import NOMS_SCENARIOS, injecter, lire_etat

    etat = _etat()
    try:
        pipeline = lire_etat()
        st.caption(f"Pipeline de démo (simulé) : {pipeline.get('statut', 'inconnu')}")
    except (OSError, ValueError, KeyError):
        st.caption("Pipeline de démo absent : il sera créé à la première injection.")
    scenario = st.selectbox("Scénario", NOMS_SCENARIOS, key="rappel_scenario")
    restants = logique.REJEUX_MAX - etat["rappel_rejeux"]
    if st.button(f"Injecter la panne et enquêter ({restants} essai(s) restant(s))", key="rappel_injecter",
                 disabled=not logique.peut_rejouer(etat["rappel_rejeux"])):
        etat["rappel_rejeux"] += 1
        identifiant = logique.identifiant_incident(scenario, etat["rappel_rejeux"])
        try:
            injecter(scenario)
            client.creer_depuis_demo(identifiant)
        except ErreurApi as erreur:
            st.error(str(erreur))
            return
        except (OSError, ValueError):
            st.error("Injection impossible dans le pipeline de démo.")
            return
        etat["rappel_incidents"].append(identifiant)
        etat["rappel_evenements"][identifiant] = []
        etat["rappel_courant"] = identifiant
    if not logique.peut_rejouer(etat["rappel_rejeux"]):
        st.info(f"Plafond de {logique.REJEUX_MAX} enquêtes par session atteint (limite les appels au modèle).")


@st.fragment(run_every=logique.INTERVALLE_RAFRAICHISSEMENT_S)
def zone_fil(client: ClientApi) -> None:
    st.subheader("2. Fil d'enquête en direct")
    etat = _etat()
    identifiant = etat.get("rappel_courant")
    if not identifiant:
        st.caption("Aucune enquête lancée.")
        return
    existants = etat["rappel_evenements"].get(identifiant, [])
    try:
        nouveaux = client.fil(identifiant, logique.derniere_etape(existants))
        incident = client.lire_incident(identifiant)
    except ErreurApi as erreur:
        st.warning(logique.MESSAGE_API_COUPEE if erreur.statut is None else str(erreur))
        return
    evenements = logique.fusionner(existants, nouveaux)
    etat["rappel_evenements"][identifiant] = evenements
    etat.setdefault("rappel_resultats", {})[identifiant] = incident
    mention = logique.mention_simulation(incident)
    if mention:
        st.caption(mention)
    affiches = evenements
    bornes = logique.bornes_curseur(evenements)
    if logique.enquete_terminee(incident) and bornes:
        # Rejeu en lecture seule : le fil est affiché jusqu'à l'étape choisie, rien n'est recalculé.
        etape = st.slider("Étape (rejeu)", min_value=bornes[0], max_value=bornes[1], value=bornes[1],
                          key=f"rappel_etape_{identifiant}")
        affiches = logique.rejouer_jusqua(evenements, etape)
    for evenement in affiches:
        afficher_evenement(evenement, bool(incident.get("enquete_simulee")))
    if not logique.enquete_terminee(incident):
        st.caption("Enquête en cours…")


def zone_resultat(client: ClientApi) -> None:
    st.subheader("3. Résultat et compteur")
    etat = _etat()
    identifiant = etat.get("rappel_courant")
    incident = etat.get("rappel_resultats", {}).get(identifiant)
    st.button("Rafraîchir le résultat", key="rappel_rafraichir")
    st.write(logique.resume_incident(incident))
    if logique.enquete_terminee(incident) and incident.get("correctif"):
        colonne_a, colonne_b = st.columns(2)
        for colonne, decision in ((colonne_a, "appliquer"), (colonne_b, "refuser")):
            if colonne.button(decision.capitalize(), key=f"rappel_{decision}_{identifiant}"):
                try:
                    reponse = client.decision(identifiant, decision)
                    st.info(str(reponse.get("message", "Décision enregistrée.")))
                except ErreurApi as erreur:
                    st.error(str(erreur))
    incidents = etat["rappel_incidents"]
    if len(incidents) >= 2:
        a = st.selectbox("Incident de référence (P1)", incidents, index=0, key="rappel_cmp_a")
        b = st.selectbox("Incident comparé (P2)", incidents, index=len(incidents) - 1, key="rappel_cmp_b")
        if a != b:
            try:
                comparaison = client.comparaison(a, b)
            except ErreurApi as erreur:
                st.caption(str(erreur))
                return
            st.table(logique.lignes_compteur(comparaison))
            st.write(logique.verdict_regle_60pc(comparaison))
            st.caption(comparaison.get("note", ""))


def zone_relations(client: ClientApi) -> None:
    st.subheader("4. Relations")
    identifiant = _etat().get("rappel_courant")
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
    if hasattr(st, "graphviz_chart"):
        st.graphviz_chart(logique.dot_relations(graphe))
    else:
        st.code(graphe.get("mermaid", ""), language="mermaid")
    st.table(logique.tableau_relations(graphe))


def afficher(url_api: str) -> None:
    client = ClientApi(url_api)
    zone_pipeline(client)
    zone_fil(client)
    zone_resultat(client)
    zone_relations(client)
