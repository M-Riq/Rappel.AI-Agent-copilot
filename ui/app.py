"""Navigation de l'espace de travail Rappel et exemple P1 en lecture seule."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests
import streamlit as st

from rappel.config import RACINE, charger
import logique
from ui.traductions import LANGUES, traduire


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
    import zones

    type_evenement = str(evenement.get("type", "information"))
    st.caption(zones.texte("Étape {etape} · {horodatage} · {type_evenement}",
                            etape=evenement.get("etape", "?"), horodatage=evenement.get("horodatage", ""),
                            type_evenement=zones.texte(type_evenement)))
    etiquette = logique.etiquette_debat(evenement, zones.langue_interface())
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
        st.text(zones.texte("Modèle : {modele}", modele=evenement["modele"]))
    if evenement.get("requete_sql"):
        st.code(str(evenement["requete_sql"]), language="sql")


def afficher_exemple():
    import zones

    st.title(zones.texte("Exemple P1"))
    st.caption(zones.texte("Exemple en lecture seule, indépendant des incidents de la session."))
    try:
        configuration = charger()
    except (ValueError, TypeError):
        st.error(zones.texte("Configuration invalide. Vérifiez les paramètres de l'application."))
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
                st.text(zones.texte("Mode API — {modes}", modes=" · ".join(modes)))
    except (requests.RequestException, ValueError, TypeError):
        pass
    try:
        evenements, hors_ligne = charger_evenements(configuration)
    except (OSError, ValueError, TypeError):
        st.error(zones.texte("Événements indisponibles : la fixture locale est absente ou invalide."))
        return
    if hors_ligne:
        st.warning(zones.texte("API coupée ou indisponible : affichage des événements de la fixture locale."))
    evenements = zones.filtres_affichage(trier_evenements(evenements), "rappel_exemple", (("type", "Types d'événement"),))
    for evenement in zones.pagination_affichage(evenements, "rappel_exemple"):
        afficher_evenement(evenement)


def main():
    import zones

    st.set_page_config(page_title="Rappel", layout="wide", initial_sidebar_state="auto")
    langue = zones.langue_interface()
    st.html(
        f"<script>document.documentElement.lang = '{langue}'; document.body.lang = '{langue}';</script>",
        unsafe_allow_javascript=True,
    )
    vues = [
        ("pages/0_Incidents.py", "Incidents", "format_list_bulleted", "incidents"),
        ("pages/1_Enquete_en_direct.py", "Enquête", "manage_search", "enquete"),
        ("pages/2_Actions.py", "Actions", "task_alt", "actions"),
        ("pages/3_Memoire.py", "Mémoire", "history", "memoire"),
        ("pages/4_Compteurs.py", "Compteurs", "bar_chart", "compteurs"),
        ("pages/5_Relations.py", "Relations", "account_tree", "relations"),
        ("pages/6_Exemple_P1.py", "Exemple P1", "science", "exemple"),
    ]
    pages = [st.Page(chemin, title=traduire(titre, langue), icon=f":material/{icone}:", url_path=url, default=index == 0)
             for index, (chemin, titre, icone, url) in enumerate(vues)]
    navigation = st.navigation({"Rappel": pages[:3], traduire("Analyse", langue): pages[3:6],
                                traduire("Exemple", langue): pages[6:]},
                               position="sidebar", expanded=True)
    st.sidebar.selectbox("Langue / Language", tuple(LANGUES), format_func=LANGUES.__getitem__,
                          key="rappel_langue")
    try:
        configuration = charger()
    except (ValueError, TypeError):
        st.error(zones.texte("Configuration invalide. Vérifiez les paramètres de l'application."))
        return
    zones.barre_laterale(zones.ClientApi(configuration.api_url))
    navigation.run()


if __name__ == "__main__":
    main()
