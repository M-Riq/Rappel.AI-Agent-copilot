"""Suivi des incidents via l'API ou consultation explicite de la fixture P1."""

import json
from pathlib import Path
import sys
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests
import streamlit as st
from jsonschema import ValidationError

from rappel.config import RACINE, charger
from rappel.contrats import valider


def _evenements(donnees):
    if isinstance(donnees, dict):
        donnees = donnees.get("evenements")
    if not isinstance(donnees, list) or not all(
        isinstance(evenement, dict) for evenement in donnees
    ):
        raise ValueError("Format d'événements invalide")
    return donnees


def charger_fixture(nom):
    with (RACINE / "fixtures" / nom).open(encoding="utf-8") as fichier:
        return json.load(fichier)


def charger_evenements(configuration, incident_id="p1", repli_fixture=False):
    try:
        reponse = requests.get(
            f"{configuration.api_url.rstrip('/')}/incidents/{quote(incident_id, safe='')}/evenements",
            timeout=5,
        )
        reponse.raise_for_status()
        return _evenements(reponse.json()), False
    except (requests.RequestException, ValueError, TypeError):
        if not repli_fixture or incident_id != "p1":
            raise RuntimeError("Événements indisponibles pour cet incident.") from None
        return _evenements(charger_fixture("evenements_p1.json")), True


def charger_sante(configuration):
    try:
        reponse = requests.get(
            f"{configuration.api_url.rstrip('/')}/healthz", timeout=5
        )
        reponse.raise_for_status()
        sante = reponse.json()
        return sante if isinstance(sante, dict) else {}
    except (requests.RequestException, ValueError, TypeError):
        return {}


def trier_evenements(evenements):
    def cle(evenement):
        etape = evenement.get("etape", 0)
        try:
            ordre = (0, float(etape))
        except (TypeError, ValueError):
            ordre = (1, str(etape))
        return ordre, str(evenement.get("horodatage", ""))

    return sorted(evenements, key=cle)


def afficher_evenement(evenement):
    type_evenement = str(evenement.get("type", "information"))
    st.caption(
        f"Étape {evenement.get('etape', '?')} · "
        f"{evenement.get('horodatage', '')} · {type_evenement}"
    )
    texte = str(
        evenement.get("texte", evenement.get("message", evenement.get("contenu", "")))
    )
    afficher = {
        "erreur": st.error,
        "error": st.error,
        "rejet": st.error,
        "alerte": st.warning,
        "abstention": st.warning,
        "warning": st.warning,
        "debat": st.warning,
        "succes": st.success,
        "success": st.success,
        "conclusion": st.success,
        "decision": st.success,
        "preuve": st.success,
    }.get(type_evenement.lower(), st.info)
    afficher(texte)
    if evenement.get("modele"):
        st.text(f"Modèle : {evenement['modele']}")
    if evenement.get("requete_sql"):
        st.code(str(evenement["requete_sql"]), language="sql")
    elif type_evenement.lower() == "preuve":
        st.warning("Preuve incomplète : requête SQL non transmise.")


def envoyer_decision(configuration, incident_id, decision):
    try:
        reponse = requests.post(
            f"{configuration.api_url.rstrip('/')}/correctifs/{quote(incident_id, safe='')}/decision",
            json={"decision": decision},
            timeout=5,
        )
        reponse.raise_for_status()
        resultat = reponse.json()
        if (
            not isinstance(resultat, dict)
            or resultat.get("id") != incident_id
            or resultat.get("decision") != decision
            or resultat.get("execute") is not False
        ):
            raise ValueError("Réponse de décision invalide.")
    except (requests.RequestException, ValueError, TypeError):
        raise RuntimeError(
            "Décision non confirmée. Vérifier son état côté API avant tout nouvel envoi."
        ) from None


def afficher_action(configuration, incident_id, sante, simulation, evenements):
    st.subheader("Action")
    if any(evenement.get("type") == "abstention" for evenement in evenements):
        st.warning("Abstention — aucun correctif proposé.")
        return
    if not evenements or incident_id != "p1" or (not simulation and sante.get("llm") != "fake"):
        st.info("Aucun correctif disponible via l'API actuelle.")
        return
    try:
        correctif = charger_fixture("correctif_p1.json")
        valider("correctif_propose", correctif)
    except (OSError, ValueError, TypeError, ValidationError):
        st.error("Correctif de démonstration absent ou invalide.")
        return
    st.caption("Correctif de démonstration P1")
    st.write(correctif["description"])
    st.metric("Risque", correctif["niveau_risque"])
    st.metric("Confiance", f"{correctif['confiance']:.0%}")
    st.text("Réversible : " + ("oui" if correctif["reversible"] else "non"))
    st.write("Éléments touchés : " + ", ".join(correctif["elements_touches"]))
    with st.expander("Patch et justification"):
        st.code(correctif["patch"], language="diff")
        st.write(correctif["justification"])
    st.caption("Phase 0/1 : décision enregistrée uniquement, aucun patch exécuté.")
    cle = f"decision:{configuration.api_url}:{incident_id}"
    resultat = st.session_state.get(cle)
    autorise = (
        not simulation
        and sante.get("llm") == "fake"
        and sante.get("source") == "fichier"
        and resultat is None
    )

    def decider(decision):
        st.session_state[cle] = {"decision": decision, "confirmee": False}
        try:
            envoyer_decision(configuration, incident_id, decision)
        except RuntimeError:
            return
        st.session_state[cle]["confirmee"] = True

    st.button(
        "Appliquer", icon=":material/check:", key="appliquer",
        disabled=not autorise or correctif["niveau_risque"] == "refus" or not correctif["reversible"],
        on_click=decider, args=("appliquer",),
    )
    st.button(
        "Refuser", icon=":material/close:", key="refuser",
        disabled=not autorise, on_click=decider, args=("refuser",),
    )
    if resultat and not simulation:
        if resultat["confirmee"]:
            st.success(f"Décision « {resultat['decision']} » enregistrée. Aucun patch exécuté.")
        else:
            st.warning("Décision non confirmée. Vérifier son état côté API avant tout nouvel envoi.")


def afficher_suivi(configuration, incident_id, simulation):
    if simulation:
        st.warning("Simulation locale P1 — aucun appel aux sponsors.")
        sante = {}
    else:
        sante = charger_sante(configuration)
        modes = [
            f"{nom} : {sante[nom]}"
            for nom in ("llm", "source", "memoire")
            if sante.get(nom)
        ]
        if modes:
            st.text(f"Mode API — {' · '.join(modes)}")
        if sante.get("llm") == "fake":
            st.warning("Simulation API — aucun appel réel à Nemotron.")
    try:
        evenements = (
            _evenements(charger_fixture("evenements_p1.json"))
            if simulation
            else charger_evenements(configuration, incident_id)[0]
        )
    except (OSError, ValueError, TypeError, RuntimeError):
        st.error("API coupée, incident indisponible ou données invalides.")
        return
    if any(evenement.get("incident_id", incident_id) != incident_id for evenement in evenements):
        st.error("Les événements reçus ne correspondent pas à l'incident sélectionné.")
        return
    fil, memoire, action = st.columns([2, 1, 1], gap="large")
    with fil:
        st.subheader("Enquête")
        st.caption(f"{len(evenements)} événements reçus")
        if not evenements:
            st.info("Aucun événement disponible pour cet incident.")
        for evenement in trier_evenements(evenements):
            afficher_evenement(evenement)
    with memoire:
        st.subheader("Mémoire")
        st.info("Incidents consultés et pistes évitées : données non transmises par l'API.")
    with action:
        afficher_action(configuration, incident_id, sante, simulation, evenements)


def main():
    st.set_page_config(page_title="Rappel — suivi des incidents", layout="wide")
    st.title("Rappel")
    try:
        configuration = charger()
    except (ValueError, TypeError):
        st.error("Configuration invalide. Vérifiez les paramètres de l'application.")
        return
    source = st.radio("Source", ["API", "Fixture locale P1"], horizontal=True)
    simulation = source == "Fixture locale P1"
    incident_id = st.text_input(
        "Identifiant de l'incident", value="p1", max_chars=160, disabled=simulation,
        key="incident_fixture" if simulation else "incident_api",
    ).strip()
    if simulation:
        incident_id = "p1"
    if not incident_id:
        st.info("Aucun incident sélectionné.")
        return
    automatique = st.toggle("Actualisation toutes les 5 secondes", value=False, disabled=simulation)
    st.button("Actualiser", icon=":material/refresh:", disabled=simulation)

    @st.fragment(run_every=5 if automatique and not simulation else None)
    def suivre():
        afficher_suivi(configuration, incident_id, simulation)

    suivre()


if __name__ == "__main__":
    main()
