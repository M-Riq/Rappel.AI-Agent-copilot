"""Logique pure de l'interface (aucun import de Streamlit ni de réseau) : testable hors interface."""


def filtrer_lignes(lignes: list[dict], recherche: str = "", selections: dict | None = None) -> list[dict]:
    termes = recherche.casefold().split()
    selections = selections or {}
    return [
        ligne for ligne in lignes
        if all(not valeurs or ligne.get(champ) in valeurs for champ, valeurs in selections.items())
        and all(terme in " ".join(str(valeur) for valeur in ligne.values()).casefold() for terme in termes)
    ]


def paginer(lignes: list[dict], numero: int, taille: int) -> tuple[list[dict], int, int]:
    taille = max(1, taille)
    total = max(1, (len(lignes) + taille - 1) // taille)
    numero = min(max(1, numero), total)
    debut = (numero - 1) * taille
    return lignes[debut:debut + taille], numero, total


def trier_lignes(lignes: list[dict], champ: str | None, decroissant: bool = False) -> list[dict]:
    if not champ:
        return list(lignes)

    def cle_tri(ligne):
        valeur = ligne.get(champ)
        if isinstance(valeur, (int, float)):
            return 0, valeur
        return 1, str(valeur or "").casefold()

    return sorted(lignes, key=cle_tri, reverse=decroissant)


def libelle_incident(identifiant: str, incidents: list[str], scenarios: dict[str, str]) -> str:
    if identifiant not in incidents:
        return identifiant
    numero = incidents.index(identifiant) + 1
    scenario = scenarios.get(identifiant)
    return f"{scenario.upper()} · Enquête {numero}" if scenario else f"Incident {numero}"


REJEUX_MAX = 3
PARCOURS_MAX = 3
SCENARIOS_PAR_PARCOURS = ("p1", "p2", "p3")
INTERVALLE_RAFRAICHISSEMENT_S = 2

MESSAGE_API_COUPEE = (
    "API coupée ou indisponible : lancer « uvicorn rappel.api:app --app-dir src » puis réessayer."
)

# Type d'événement -> (gravité d'affichage, libellé court).
STYLES_EVENEMENT = {
    "hypothese": ("info", "Hypothèse"),
    "preuve": ("success", "Preuve"),
    "rejet": ("error", "Rejet"),
    "debat": ("warning", "Débat"),
    "decision": ("success", "Décision"),
    "abstention": ("warning", "Abstention"),
}


def style_evenement(evenement: dict) -> tuple[str, str]:
    type_evenement = str(evenement.get("type", "")).lower()
    return STYLES_EVENEMENT.get(type_evenement, ("info", type_evenement.capitalize() or "Information"))


# Rôles de la war room (src/rappel/enquete/war_room.py) : le code arbitre, jamais le modèle.
ROLES_DEBAT = {
    "detective": "détective",
    "sceptique": "avocat du diable (sceptique)",
    "historien": "historien (mémoire)",
    "systeme": "arbitre (code)",
}
DEBUTS_ARBITRAGE = ("Objection du tour ", "War room terminee", "JSON invalide du ")


def role_debat(evenement: dict) -> str | None:
    """Rôle si l'événement appartient au débat de la war room, sinon None."""
    acteur = str(evenement.get("acteur", ""))
    if str(evenement.get("type", "")) == "debat":
        return ROLES_DEBAT.get(acteur, acteur or "inconnu")
    if acteur == "systeme" and str(evenement.get("texte", "")).startswith(DEBUTS_ARBITRAGE):
        return ROLES_DEBAT["systeme"]
    return None


def etiquette_debat(evenement: dict) -> str | None:
    role = role_debat(evenement)
    return None if role is None else f"Débat · {role}"


def badge_modele(evenement: dict, simulation: bool) -> str | None:
    """Badge honnête : jamais « Nemotron » pour un modèle simulé ou un autre modèle."""
    modele = str(evenement.get("modele") or "").strip()
    if not modele:
        return "simulé" if simulation and evenement.get("acteur") not in (None, "systeme") else None
    if simulation:
        return f"simulé · {modele}"
    if "nemotron" in modele.lower():
        return f"Nemotron via Meterless · {modele}"
    return f"Modèle hors Nemotron : {modele} (fixer la voie)"


def sql_a_afficher(evenement: dict) -> str | None:
    """Requête affichée sous une preuve (ou sous tout événement qui en porte une)."""
    requete = evenement.get("requete_sql")
    if isinstance(requete, str) and requete.strip():
        return requete.strip()
    return None


LONGUEUR_MAX_TEXTE = 5000


def texte_evenement(evenement: dict) -> str:
    """Texte affiché d'un événement, borné pour qu'un texte géant ne fige pas l'interface."""
    texte = str(evenement.get("texte", evenement.get("message", evenement.get("contenu", ""))))
    if len(texte) > LONGUEUR_MAX_TEXTE:
        return texte[:LONGUEUR_MAX_TEXTE] + f"… [{len(texte) - LONGUEUR_MAX_TEXTE} caractères masqués]"
    return texte


def _etape(evenement: dict) -> float:
    try:
        return float(evenement.get("etape", 0))
    except (TypeError, ValueError):
        return 0.0


def derniere_etape(evenements: list[dict]) -> int:
    """Valeur du paramètre « apres » pour la prochaine lecture du fil."""
    return int(max((_etape(evenement) for evenement in evenements), default=0))


def fusionner(existants: list[dict], nouveaux: list[dict]) -> list[dict]:
    """Ajoute les nouveaux événements sans doublon d'étape, dans l'ordre des étapes."""
    par_etape = {_etape(evenement): evenement for evenement in existants}
    for evenement in nouveaux:
        if isinstance(evenement, dict):
            par_etape.setdefault(_etape(evenement), evenement)
    return [par_etape[etape] for etape in sorted(par_etape)]


def rejouer_jusqua(evenements: list[dict], etape: int | None) -> list[dict]:
    """Fil rejoué jusqu'à l'étape donnée (incluse) ; lecture seule, rien n'est recalculé."""
    if etape is None:
        return list(evenements)
    return [evenement for evenement in evenements if _etape(evenement) <= etape]


def bornes_curseur(evenements: list[dict]) -> tuple[int, int] | None:
    """(première, dernière) étape pour le curseur de rejeu, ou None s'il y a moins de deux étapes."""
    etapes = sorted({int(_etape(evenement)) for evenement in evenements})
    return (etapes[0], etapes[-1]) if len(etapes) >= 2 else None


def enquete_terminee(incident: dict | None) -> bool:
    return isinstance(incident, dict) and incident.get("statut") in ("termine", "erreur")


def mention_simulation(incident: dict | None) -> str | None:
    if not isinstance(incident, dict):
        return None
    parties = []
    if incident.get("enquete_simulee"):
        parties.append("raisonnement simulé (LLM fake)")
    if incident.get("preuves_simulees"):
        parties.append(f"preuves simulées ({incident.get('source_preuves') or 'simule'})")
    return "Mode simulé : " + " ; ".join(parties) if parties else None


def style_incident(incident: dict | None) -> tuple[str, str, str]:
    incident = incident or {}
    statut = incident.get("statut")
    if statut == "termine" and (incident.get("decision_abstention") or {}).get("abstient"):
        return "Abstention", "orange", ":material/pan_tool:"
    return {
        "en_cours": ("Enquête en cours", "blue", ":material/hourglass_top:"),
        "termine": ("Enquête terminée", "green", ":material/task_alt:"),
        "erreur": ("Enquête interrompue", "red", ":material/error:"),
    }.get(statut, ("État non confirmé", "gray", ":material/help:"))


def libelle_decision(incident: dict, decision_locale: dict | None = None) -> str:
    if decision_locale and not decision_locale.get("confirmee"):
        return "Non confirmée"
    decision = incident.get("decision_humaine") or (decision_locale or {}).get("decision")
    if decision:
        return {"appliquer": "Application demandée", "refuser": "Correctif refusé"}.get(decision, "Non reconnue")
    if incident.get("statut") != "termine":
        return "Indisponible"
    if (incident.get("decision_abstention") or {}).get("abstient") or not incident.get("correctif"):
        return "Sans objet"
    return "En attente"


def resume_incident(incident: dict | None) -> str:
    if not isinstance(incident, dict):
        return "Aucun incident."
    statut = incident.get("statut", "?")
    if statut != "termine":
        return f"{style_incident(incident)[0]}."
    abstention = incident.get("decision_abstention") or {}
    if abstention.get("abstient"):
        manque = ", ".join(abstention.get("manque") or []) or "non précisé"
        return f"Abstention : aucun correctif proposé. Manque : {manque}."
    correctif = incident.get("correctif") or {}
    risque = correctif.get("niveau_risque")
    return f"Correctif proposé (risque : {risque})." if correctif else "Enquête terminée sans correctif."


def total_tokens(metriques: dict) -> int | None:
    entree = metriques.get("nb_tokens_entree")
    sortie = metriques.get("nb_tokens_sortie")
    return None if entree is None or sortie is None else entree + sortie


def lignes_metriques(incident: dict, evenements: list[dict]) -> list[dict]:
    metriques = incident.get("metriques") or {}
    mesures = [
        ("Dernière étape reçue", derniere_etape(evenements)),
        ("Événements d'enquête", metriques.get("nb_evenements")),
        ("Tours d'enquête", metriques.get("nb_tours")),
        ("Appels LLM", metriques.get("nb_appels_llm")),
        ("Appels voie rapide", metriques.get("nb_appels_rapide")),
        ("Appels voie profonde", metriques.get("nb_appels_profonde")),
        ("Tokens", total_tokens(metriques)),
        ("Durée (ms, indicative)", metriques.get("duree_ms")),
        ("Requêtes SQL", metriques.get("nb_requetes_sql")),
        ("Hypothèses écartées grâce à la mémoire", metriques.get("nb_ecartees_par_memoire")),
    ]
    return [{"mesure": nom, "valeur": "inconnue" if valeur is None else valeur} for nom, valeur in mesures]


def lignes_compteur(comparaison: dict) -> list[dict]:
    """Lignes du compteur P1 contre P2 ; la durée est indiquée comme indicative."""
    metriques = comparaison.get("metriques", {})
    a, b = metriques.get("a", {}), metriques.get("b", {})
    reductions = comparaison.get("reductions_pct", {})

    definitions = [
        ("Événements", a.get("nb_evenements"), b.get("nb_evenements"), "evenements"),
        ("Hypothèses testées", a.get("nb_hypotheses_testees"), b.get("nb_hypotheses_testees"), "hypotheses_testees"),
        ("Appels LLM", a.get("nb_appels_llm"), b.get("nb_appels_llm"), "appels_llm"),
        ("Tokens", total_tokens(a), total_tokens(b), "tokens"),
        ("Durée (ms, indicative)", a.get("duree_ms"), b.get("duree_ms"), "duree"),
        ("Tours d'enquête", a.get("nb_tours"), b.get("nb_tours"), "tours"),
        ("Requêtes SQL", a.get("nb_requetes_sql"), b.get("nb_requetes_sql"), "requetes_sql"),
    ]
    return [
        {
            "mesure": libelle,
            "a": "inconnu" if valeur_a is None else valeur_a,
            "b": "inconnu" if valeur_b is None else valeur_b,
            "reduction": "inconnue" if reductions.get(cle) is None else f"{reductions[cle]} %",
        }
        for libelle, valeur_a, valeur_b, cle in definitions
    ]


def verdict_regle_60pc(comparaison: dict) -> str:
    regle = comparaison.get("regle_60pc") or {}
    evenements, appels = regle.get("evenements_b_sur_a"), regle.get("appels_b_sur_a")
    if evenements is None or appels is None:
        return "Règle des 60 % : non mesurable."
    etat = "respectée" if regle.get("respecte") else "non respectée"
    texte = f"Règle des 60 % {etat} : événements {evenements:.0%} de P1, appels LLM {appels:.0%}."
    if comparaison.get("simulation"):
        texte += " (simulé)"
    return texte


def signature_incident(graphe: dict, identifiant: str) -> str | None:
    signatures = {
        noeud["id"] for noeud in graphe.get("noeuds", [])
        if noeud.get("type") == "signature" and noeud.get("id", "").startswith("signature:")
    }
    for arete in graphe.get("aretes", []):
        if (arete.get("source") == f"incident:{identifiant}"
                and arete.get("type") == "a_pour_signature" and arete.get("cible") in signatures):
            return arete["cible"].removeprefix("signature:")
    return None


def evenements_memoire(evenements: list[dict]) -> list[dict]:
    return [
        evenement for evenement in evenements
        if evenement.get("acteur") == "historien" or (
            evenement.get("acteur") == "systeme"
            and evenement.get("type") in ("decision", "rejet")
            and any(mention in str(evenement.get("texte", "")).casefold() for mention in (
                "incident similaire", "mémoire", "memoire", "correctif déjà essayé",
            ))
        )
    ]


def lignes_souvenirs(memoire: dict, signature: str) -> list[dict]:
    statuts = memoire.get("statuts_resultat", [])
    libelles = {"reussi": "réussi", "echoue": "échoué", "non_confirme": "non confirmé"}
    return [
        {
            "date": souvenir.get("date", "inconnue"),
            "cause": souvenir.get("cause_confirmee", "inconnue"),
            "correctif essayé": souvenir.get("correctif_essaye", "inconnu"),
            "résultat": libelles.get(statuts[indice] if indice < len(statuts) else None, "non confirmé"),
        }
        for indice, souvenir in enumerate(memoire.get("souvenirs", []))
        if souvenir.get("signature") == signature
    ]


def peut_rejouer(nb_rejeux: int, maximum: int = REJEUX_MAX) -> bool:
    return nb_rejeux < maximum


def plafond_enquetes(simulation: bool, accord_reel: bool = False) -> int:
    return PARCOURS_MAX * len(SCENARIOS_PAR_PARCOURS) if simulation or accord_reel else REJEUX_MAX


def identifiant_incident(scenario: str, numero: int, session: str | None = None) -> str:
    suffixe = f"-{session}" if session else ""
    return f"ui-{scenario}-{numero}{suffixe}"


# Graphe de relations (GET /graphe) : affichage Graphviz (DOT) ou tableau, sans dépendance.
COULEURS_NOEUDS = {
    "incident": "#4c78a8", "signature": "#9d755d", "hypothese_retenue": "#54a24b",
    "hypothese_refutee": "#e45756", "cause": "#f58518", "correctif": "#b279a2",
}
LIBELLES_RELATIONS = {
    "a_pour_signature": "a pour signature", "meme_signature": "partage la même signature",
    "retenue_dans": "hypothèse retenue dans", "refutee_dans": "hypothèse réfutée dans",
    "refutee_pour": "réfutée pour (mémoire)", "cause_confirmee_de": "cause confirmée de",
    "correctif_essaye_pour": "correctif essayé pour", "resultat_reussi": "résultat réussi",
    "resultat_echoue": "résultat échoué", "resultat_non_confirme": "résultat non confirmé",
}


def _dot(texte) -> str:
    return str(texte).replace("\\", "\\\\").replace('"', "'")


def dot_relations(graphe: dict) -> str:
    noeuds = graphe.get("noeuds") or []
    if not noeuds:
        return 'digraph relations { vide [label="aucune relation"]; }'
    alias = {noeud["id"]: f"n{indice}" for indice, noeud in enumerate(noeuds)}
    lignes = ["digraph relations {", "  rankdir=LR;", '  node [style=filled, fontcolor=white, fontsize=10];']
    for noeud in noeuds:
        couleur = COULEURS_NOEUDS.get(noeud.get("type"), "#777777")
        lignes.append(f'  {alias[noeud["id"]]} [label="{_dot(noeud.get("libelle", ""))}", fillcolor="{couleur}"];')
    for arete in graphe.get("aretes") or []:
        if arete["source"] in alias and arete["cible"] in alias:
            libelle = LIBELLES_RELATIONS.get(arete["type"], arete["type"])
            lignes.append(f'  {alias[arete["source"]]} -> {alias[arete["cible"]]} [label="{_dot(libelle)}", fontsize=9];')
    lignes.append("}")
    return "\n".join(lignes)


def tableau_relations(graphe: dict) -> list[dict]:
    libelles = {noeud["id"]: f'{noeud.get("type")} : {noeud.get("libelle")}' for noeud in graphe.get("noeuds") or []}
    return [{"source": libelles.get(arete["source"], arete["source"]),
             "relation": LIBELLES_RELATIONS.get(arete["type"], arete["type"]),
             "cible": libelles.get(arete["cible"], arete["cible"])}
            for arete in graphe.get("aretes") or []]
