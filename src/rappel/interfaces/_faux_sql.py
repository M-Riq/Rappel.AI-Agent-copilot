"""Reponses deterministes du LLM faux pour la boucle d'enquete SQL (hors reseau).

Aucune valeur de scenario : les requetes sont generiques et les conclusions sont
deduites uniquement des resultats SQL et du resume de contexte fournis dans le prompt.
"""
import json


SEUIL_COMPTE_LOGS = 5
ETATS_SAINS = ("passed", "healthy", "recovered", "success")
MOTS_PAR_CATEGORIE = {
    "schema": ("rename", "renomm", "column", "colonne", "schema", "type"),
    "ressources": ("memory", "memoire", "disk", "disque", "oom"),
    "reseau": ("timeout", "connection", "connexion", "network", "reseau"),
    "configuration": ("config", "parametre", "parameter"),
}
SQL_ECART_SCHEMA = (
    "SELECT 'absente' AS ecart, a.nom AS nom, a.type AS type_attendu, NULL AS type_observe "
    "FROM colonnes_attendues a WHERE a.nom NOT IN (SELECT nom FROM colonnes_observees) "
    "UNION ALL "
    "SELECT 'type', a.nom, a.type, o.type FROM colonnes_attendues a "
    "JOIN colonnes_observees o ON o.nom = a.nom "
    "WHERE a.type <> o.type AND a.type <> '?' AND o.type <> '?' "
    "UNION ALL "
    "SELECT 'nouvelle', o.nom, NULL, o.type FROM colonnes_observees o "
    "WHERE o.nom NOT IN (SELECT nom FROM colonnes_attendues)"
)


def _contient(colonne: str, mots: tuple[str, ...]) -> str:
    return " OR ".join(f"lower({colonne}) LIKE '%{mot}%'" for mot in mots)


def sql_logs(mots: tuple[str, ...]) -> str:
    return (
        f"SELECT template, compte FROM logs WHERE compte > {SEUIL_COMPTE_LOGS} "
        f"AND lower(template) NOT LIKE 'info%' AND NOT ({_contient('template', ETATS_SAINS)}) "
        f"AND ({_contient('template', mots)}) ORDER BY compte DESC"
    )


def sql_commits(mots: tuple[str, ...]) -> str:
    return f"SELECT hash, message, date FROM commits WHERE {_contient('message', mots)} ORDER BY date DESC"


CANDIDATS = (
    ("Le schema des donnees a change", "schema", SQL_ECART_SCHEMA),
    ("Saturation des ressources", "ressources", sql_logs(MOTS_PAR_CATEGORIE["ressources"])),
    ("Probleme reseau ou timeout", "reseau", sql_logs(MOTS_PAR_CATEGORIE["reseau"])),
    ("Changement de configuration", "configuration", sql_commits(MOTS_PAR_CATEGORIE["configuration"])),
)


def _non_vides(hypothese: dict) -> list[dict]:
    return [requete for requete in hypothese.get("requetes", [])
            if not requete.get("erreur") and requete.get("nb_lignes", 0) > 0]


def _hypotheses() -> str:
    return json.dumps([{"texte": texte, "categorie": categorie, "sql": sql}
                       for texte, categorie, sql in CANDIDATS], ensure_ascii=False)


def _verdict(donnees: dict) -> str:
    hypotheses = donnees.get("hypotheses", [])
    verdicts = []
    for hypothese in hypotheses:
        preuves = _non_vides(hypothese)
        verdicts.append({
            "id": hypothese["id"],
            "statut": "retenue" if preuves else "refutee",
            "requetes_citees": [requete["id"] for requete in preuves],
            "raison": "resultats non vides" if preuves else "requetes sans resultat",
        })
    reponse = {"verdicts": verdicts, "requetes_suivi": []}
    meilleure = max(hypotheses, key=lambda hypothese: len(_non_vides(hypothese)), default=None)
    # Sans incident similaire en memoire, une preuve unique est corroboree par une autre table.
    if (meilleure is not None and not donnees.get("incident_similaire")
            and donnees.get("suivis_restants", 0) > 0 and len(_non_vides(meilleure)) == 1
            and not any(requete.get("origine") == "suivi" for requete in meilleure.get("requetes", []))):
        mots = MOTS_PAR_CATEGORIE.get(meilleure.get("categorie") or "", ())
        if mots:
            reponse["requetes_suivi"] = [{"hypothese_id": meilleure["id"], "sql": sql_commits(mots)}]
    return json.dumps(reponse, ensure_ascii=False)


def _ecarts(hypothese: dict) -> dict[str, list[list]]:
    ecarts: dict[str, list[list]] = {"absente": [], "type": [], "nouvelle": []}
    for requete in hypothese.get("requetes", []):
        if requete.get("colonnes", [])[:1] != ["ecart"]:
            continue
        for ligne in requete.get("lignes", []):
            if ligne and ligne[0] in ecarts:
                ecarts[ligne[0]].append(ligne)
    return ecarts


def _cause(hypothese: dict) -> str:
    ecarts = _ecarts(hypothese)
    absentes = [ligne[1] for ligne in ecarts["absente"]]
    nouvelles = [ligne[1] for ligne in ecarts["nouvelle"]]
    if absentes and nouvelles:
        return f"colonne renommée ({', '.join(absentes)} -> {', '.join(nouvelles)})"
    if ecarts["type"]:
        return "type de colonne modifié (" + ", ".join(
            f"{nom} : attendu {attendu}, observé {observe}" for _, nom, attendu, observe in ecarts["type"]
        ) + ")"
    if absentes:
        return f"colonne manquante ({', '.join(absentes)})"
    if nouvelles:
        return f"nouvelle colonne inattendue ({', '.join(nouvelles)})"
    return f"cause confirmée : {hypothese.get('texte', '')}"


def _preuves_schema(hypothese: dict) -> list[str]:
    ecarts = _ecarts(hypothese)
    return (
        [f"schema: colonne manquante {nom}:{attendu}" for _, nom, attendu, _ in ecarts["absente"]]
        + [f"schema: type different pour {nom}: attendu {attendu}, observe {observe}"
           for _, nom, attendu, observe in ecarts["type"]]
        + [f"schema: nouvelle colonne {nom}:{observe}" for _, nom, _, observe in ecarts["nouvelle"]]
    )


def _conclusion(donnees: dict) -> str:
    hypothese = donnees["hypothese"]
    return json.dumps({
        "cause_confirmee": _cause(hypothese),
        "requetes_citees": [requete["id"] for requete in _non_vides(hypothese)],
    }, ensure_ascii=False)


def _correctif(donnees: dict) -> str:
    hypothese = donnees["hypothese"]
    contexte = donnees.get("contexte", {})
    composant = contexte.get("composant", "")
    cause = donnees.get("cause_confirmee") or _cause(hypothese)
    citees = [requete["id"] for requete in _non_vides(hypothese)]
    preuves_schema = _preuves_schema(hypothese)
    if hypothese.get("categorie") == "schema" and preuves_schema:
        attendu = contexte.get("schema_attendu", [])
        description = f"Restaurer le schema attendu de {composant}: {', '.join(attendu)}"
        patch = json.dumps({"composant": composant, "avant": contexte.get("schema_observe", []),
                            "apres": attendu}, indent=2)
        justification = f"{cause}: {'; '.join(preuves_schema)} (requetes {', '.join(citees)})"
    else:
        description = f"Examiner et corriger {hypothese.get('categorie') or hypothese.get('texte')} pour {composant} apres validation"
        patch = json.dumps({"composant": composant, "hypothese": hypothese.get("texte"), "requetes": citees})
        justification = f"{cause} (requetes {', '.join(citees)})"
    reponse = {
        "description": description, "patch": patch, "elements_touches": [composant],
        "reversible": True, "justification": justification,
        "confiance": donnees.get("confiance", 0.0),
    }
    if donnees.get("inclure_conclusion"):
        reponse["cause_confirmee"] = cause
    return json.dumps(reponse, ensure_ascii=False)


def repondre(role: str, donnees: dict) -> str:
    if role == "hypotheses":
        return _hypotheses()
    if role == "verdict":
        return _verdict(donnees)
    if role == "conclusion":
        return _conclusion(donnees)
    if role == "correctif":
        return _correctif(donnees)
    raise ValueError(f"Role LLM inconnu : {role}")
