"""Logique pure de l'interface (aucun import de Streamlit ni de réseau) : testable hors interface."""

REJEUX_MAX = 3
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


def resume_incident(incident: dict | None) -> str:
    if not isinstance(incident, dict):
        return "Aucun incident."
    statut = incident.get("statut", "?")
    if statut != "termine":
        return f"Enquête {statut}."
    abstention = incident.get("decision_abstention") or {}
    if abstention.get("abstient"):
        manque = ", ".join(abstention.get("manque") or []) or "non précisé"
        return f"Abstention : aucun correctif proposé. Manque : {manque}."
    correctif = incident.get("correctif") or {}
    risque = correctif.get("niveau_risque")
    return f"Correctif proposé (risque : {risque})." if correctif else "Enquête terminée sans correctif."


def lignes_compteur(comparaison: dict) -> list[dict]:
    """Lignes du compteur P1 contre P2 ; la durée est indiquée comme indicative."""
    metriques = comparaison.get("metriques", {})
    a, b = metriques.get("a", {}), metriques.get("b", {})
    reductions = comparaison.get("reductions_pct", {})

    def tokens(m):
        if m.get("nb_tokens_entree") is None or m.get("nb_tokens_sortie") is None:
            return None
        return m["nb_tokens_entree"] + m["nb_tokens_sortie"]

    definitions = [
        ("Événements", a.get("nb_evenements"), b.get("nb_evenements"), "evenements"),
        ("Hypothèses testées", a.get("nb_hypotheses_testees"), b.get("nb_hypotheses_testees"), "hypotheses_testees"),
        ("Appels LLM", a.get("nb_appels_llm"), b.get("nb_appels_llm"), "appels_llm"),
        ("Tokens", tokens(a), tokens(b), "tokens"),
        ("Durée (ms, indicative)", a.get("duree_ms"), b.get("duree_ms"), "duree"),
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


def peut_rejouer(nb_rejeux: int, maximum: int = REJEUX_MAX) -> bool:
    return nb_rejeux < maximum


def identifiant_incident(scenario: str, numero: int) -> str:
    return f"ui-{scenario}-{numero}"


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
