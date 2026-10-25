"""Interface de démonstration réelle : aucun repli sur les fixtures."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests
import streamlit as st
from jsonschema import ValidationError

from rappel.config import charger
from rappel.contrats import valider


def lire_contexte_json(contenu):
    try:
        contexte = json.loads(contenu)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        raise ValueError("Le fichier doit contenir un document JSON valide.") from None
    try:
        valider("contexte_incident", contexte)
    except ValidationError:
        raise ValueError(
            "Le contexte ne respecte pas le contrat contexte_incident."
        ) from None
    return contexte


def verifier_configuration_reelle(configuration, sante):
    manquants = []
    for nom, valeur in (
        ("LLM_PROVIDER=meterless", configuration.llm_provider == "meterless"),
        ("SOURCE_PROVIDER=zetaris", configuration.source_provider == "zetaris"),
        ("MEMOIRE_PROVIDER=postgres", configuration.memoire_provider == "postgres"),
        ("METERLESS_RUNTIME_TOKEN", bool(configuration.meterless_runtime_token)),
        ("METERLESS_MODEL_RAPIDE", bool(configuration.meterless_model_rapide)),
        ("METERLESS_MODEL_PROFOND", bool(configuration.meterless_model_profond)),
        ("ZETARIS_API_KEY", bool(configuration.zetaris_api_key)),
        ("ZETARIS_ORG_ID", bool(configuration.zetaris_org_id)),
    ):
        if not valeur:
            manquants.append(nom)

    if not isinstance(sante, dict) or any(
        sante.get(nom) != valeur
        for nom, valeur in (
            ("llm", "meterless"),
            ("source", "zetaris"),
            ("memoire", "postgres"),
        )
    ):
        manquants.append("modes réels confirmés par /healthz")
    return manquants


def soumettre_incident(api_url, contexte):
    try:
        reponse = requests.post(
            f"{api_url.rstrip('/')}/incidents",
            json=contexte,
            timeout=10,
        )
        reponse.raise_for_status()
        donnees = reponse.json()
    except requests.RequestException:
        raise RuntimeError(
            "Impossible de soumettre l'incident à l'API locale."
        ) from None
    except (ValueError, TypeError):
        raise RuntimeError("Réponse invalide de l'API locale.") from None
    identifiant = donnees.get("id") if isinstance(donnees, dict) else None
    if not isinstance(identifiant, str) or not identifiant:
        raise RuntimeError("L'API n'a pas retourné d'identifiant d'incident.")
    return identifiant


def charger_json_api(url):
    try:
        reponse = requests.get(url, timeout=5)
        reponse.raise_for_status()
        return reponse.json()
    except requests.RequestException:
        raise RuntimeError("API locale indisponible.") from None
    except (ValueError, TypeError):
        raise RuntimeError("Réponse JSON invalide de l'API locale.") from None


def verifier_resultat_reel(resultat):
    return (
        isinstance(resultat, dict)
        and resultat.get("statut") == "termine"
        and resultat.get("llm") == "meterless"
        and resultat.get("source_preuves") == "zetaris"
        and resultat.get("preuves_simulees") is False
        and resultat.get("enquete_simulee") is False
        and resultat.get("memoire_mode") == "postgres"
    )


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
        st.text(f"Modèle effectif : {evenement['modele']}")
    if evenement.get("requete_sql"):
        st.code(str(evenement["requete_sql"]), language="sql")


def afficher_incident(api_url, identifiant):
    try:
        resultat = charger_json_api(f"{api_url.rstrip('/')}/incidents/{identifiant}")
    except RuntimeError as erreur:
        st.error(str(erreur))
        return

    statut = resultat.get("statut") if isinstance(resultat, dict) else None
    st.subheader(f"Incident {identifiant}")
    if statut == "en_cours":
        st.info("Enquête en cours. Actualisez la page pour consulter les étapes.")
        return
    if statut == "erreur":
        st.error("L'enquête a échoué. Vérifiez la configuration et les services.")
        return
    if not verifier_resultat_reel(resultat):
        st.error(
            "Provenance réelle non confirmée : résultat masqué. "
            "Aucune simulation ne sera présentée comme une enquête réelle."
        )
        return

    st.success("Enquête réelle confirmée : Meterless · Zetaris · Postgres")
    st.json(resultat)
    try:
        evenements = charger_json_api(
            f"{api_url.rstrip('/')}/incidents/{identifiant}/enquete/evenements"
        )
    except RuntimeError as erreur:
        st.error(str(erreur))
        return
    if not isinstance(evenements, list) or not all(
        isinstance(evenement, dict) for evenement in evenements
    ):
        st.error("Format du fil d'enquête invalide.")
        return
    st.subheader("Fil d'enquête et preuves Zetaris")
    for evenement in trier_evenements(evenements):
        afficher_evenement(evenement)


def main():
    st.set_page_config(page_title="Rappel — démo réelle")
    st.title("Rappel — enquête réelle")
    st.caption(
        "Cette interface exige Meterless Nightshift, Zetaris et Postgres. "
        "Elle n'utilise pas les fixtures et ne bascule pas en mode simulé."
    )
    try:
        configuration = charger()
    except (ValueError, TypeError):
        st.error("Configuration invalide. Vérifiez le fichier .env local.")
        return

    try:
        sante = charger_json_api(f"{configuration.api_url.rstrip('/')}/healthz")
    except RuntimeError as erreur:
        st.error(f"{erreur} Aucun résultat local ou fixture ne sera affiché.")
        return

    manquants = verifier_configuration_reelle(configuration, sante)
    if manquants:
        st.error(
            "Configuration réelle incomplète ou modes non confirmés : "
            + ", ".join(manquants)
        )
        st.info("Aucun incident ne sera soumis tant que ces contrôles échouent.")
        return

    st.success("API configurée en modes réels : Meterless · Zetaris · Postgres")
    fichier = st.file_uploader(
        "Choisir le contexte JSON d'un incident réel",
        type=["json"],
        help="Ne pas utiliser une fixture pour une démo présentée comme réelle.",
    )
    if fichier is not None:
        try:
            contexte = lire_contexte_json(fichier.getvalue().decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as erreur:
            st.error(str(erreur))
            contexte = None
        if contexte is not None:
            st.caption(f"Incident à soumettre : {contexte['incident_id']}")
            if st.button("Lancer l'enquête réelle", type="primary"):
                try:
                    st.session_state["incident_id"] = soumettre_incident(
                        configuration.api_url, contexte
                    )
                except RuntimeError as erreur:
                    st.error(str(erreur))

    identifiant = st.session_state.get("incident_id")
    if identifiant:
        afficher_incident(configuration.api_url, identifiant)


if __name__ == "__main__":
    main()
