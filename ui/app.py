"""Lecture des événements P1, avec repli local si l'API est indisponible."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests
import streamlit as st

from rappel.config import RACINE, charger
import logique


def _evenements(donnees):
    if isinstance(donnees, dict):
        donnees = donnees.get("evenements")
    if not isinstance(donnees, list) or not all(
        isinstance(evenement, dict) for evenement in donnees
    ):
        raise ValueError("Format d'événements invalide")
    return donnees


def charger_evenements(configuration):
    try:
        reponse = requests.get(
            f"{configuration.api_url.rstrip('/')}/incidents/p1/evenements",
            timeout=5,
        )
        reponse.raise_for_status()
        return _evenements(reponse.json()), False
    except (requests.RequestException, ValueError, TypeError):
        with (RACINE / "fixtures" / "evenements_p1.json").open(
            encoding="utf-8"
        ) as fichier:
            return _evenements(json.load(fichier)), True


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
    etiquette = logique.etiquette_debat(evenement)
    if etiquette:
        st.caption(etiquette)
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


def main():
    st.set_page_config(page_title="Rappel — incident P1")
    st.title("Rappel — incident P1")
    try:
        configuration = charger()
    except (ValueError, TypeError):
        st.error("Configuration invalide. Vérifiez les paramètres de l'application.")
        return
    try:
        reponse = requests.get(
            f"{configuration.api_url.rstrip('/')}/healthz", timeout=5
        )
        reponse.raise_for_status()
        sante = reponse.json()
        if isinstance(sante, dict):
            modes = [
                f"{nom} : {sante[nom]}"
                for nom in ("llm", "source", "memoire")
                if sante.get(nom)
            ]
            if modes:
                st.text(f"Mode API — {' · '.join(modes)}")
    except (requests.RequestException, ValueError, TypeError):
        pass
    try:
        evenements, hors_ligne = charger_evenements(configuration)
    except (OSError, ValueError, TypeError):
        st.error("Événements indisponibles : la fixture locale est absente ou invalide.")
        return
    if hors_ligne:
        st.warning("API coupée ou indisponible : affichage des événements de la fixture locale.")
    for evenement in trier_evenements(evenements):
        afficher_evenement(evenement)
    # Désactivé par défaut : la vue P1 ci-dessus reste celle du squelette.
    if st.toggle("Enquête en direct (trois zones)", value=False):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import zones

        zones.afficher(configuration.api_url)


if __name__ == "__main__":
    main()
