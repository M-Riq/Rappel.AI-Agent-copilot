"""Graphe de relations des incidents, déterministe et sans LLM.

Entrées : souvenirs de la mémoire (EnregistrementMemoire), hypothèses enregistrées par incident
(table « hypotheses ») et, si disponibles, les incidents connus de l'API (identifiant, signature,
souvenir écrit par l'enquête).

Nœuds : incident, signature, hypothese_retenue, hypothese_refutee, cause, correctif.
Arêtes :
- a_pour_signature (incident -> signature) et meme_signature (incident -> incident suivant) ;
- retenue_dans / refutee_dans (hypothèse -> incident qui l'a testée) ;
- refutee_pour (hypothèse -> incident de même signature qui l'a écartée grâce à la mémoire) ;
- cause_confirmee_de (cause -> incident, sinon -> signature) ;
- correctif_essaye_pour (correctif -> incident, sinon -> signature) ;
- resultat_reussi / resultat_echoue / resultat_non_confirme (correctif -> cause).
Sorties : {"noeuds", "aretes"} (JSON) et texte Mermaid.
"""

import hashlib
import re

from rappel.securite import masquer_secrets

TYPES_NOEUDS = ("incident", "signature", "hypothese_retenue", "hypothese_refutee", "cause", "correctif")
LONGUEUR_LIBELLE = 80
FORMES_MERMAID = {
    "incident": ('["', '"]'), "signature": ('(["', '"])'), "hypothese_retenue": ('("', '")'),
    "hypothese_refutee": ('("', '")'), "cause": ('{{"', '"}}'), "correctif": ('[/"', '"/]'),
}


def _assainir(texte) -> str:
    texte = masquer_secrets(" ".join(str(texte).split()))
    return texte if len(texte) <= LONGUEUR_LIBELLE else texte[:LONGUEUR_LIBELLE - 3] + "..."


def _cle_texte(texte: str) -> str:
    return " ".join(str(texte).split()).casefold()


def _empreinte(texte: str) -> str:
    return hashlib.sha256(_cle_texte(texte).encode("utf-8")).hexdigest()[:12]


def _cle_souvenir(souvenir: dict) -> tuple:
    return (souvenir.get("signature"), souvenir.get("correctif_essaye"), souvenir.get("date"),
            souvenir.get("cause_confirmee"))


class _Constructeur:
    def __init__(self):
        self.noeuds: dict[str, dict] = {}
        self.aretes: dict[tuple, dict] = {}

    def noeud(self, type_noeud: str, cle: str, libelle: str, **attributs) -> str:
        identifiant = f"{type_noeud}:{cle}"
        if identifiant not in self.noeuds:
            self.noeuds[identifiant] = {"id": identifiant, "type": type_noeud, "libelle": _assainir(libelle), **attributs}
        return identifiant

    def arete(self, source: str, cible: str, type_arete: str, **attributs) -> None:
        cle = (source, cible, type_arete)
        if cle not in self.aretes:
            self.aretes[cle] = {"source": source, "cible": cible, "type": type_arete, **attributs}


def construire_graphe(souvenirs: list[dict], hypotheses: list[dict] | None = None,
                      incidents: list[dict] | None = None, statuts: list[str] | None = None) -> dict:
    """Graphe {noeuds, aretes} trié de façon déterministe.

    souvenirs : dicts EnregistrementMemoire ; statuts : reussi/echoue/non_confirme alignés sur
    souvenirs (sinon le résultat enregistré) ; hypotheses : lignes {incident_id, signature, texte,
    statut} ; incidents : {incident_id, signature, enregistrement (dict ou None)}.
    """
    graphe = _Constructeur()
    hypotheses = hypotheses or []
    incidents = incidents or []
    statuts = statuts if statuts is not None and len(statuts) == len(souvenirs) else [
        souvenir.get("resultat") for souvenir in souvenirs]

    # Incidents connus, dans l'ordre d'apparition (API puis table des hypothèses).
    ordre: dict[str, str] = {}
    for incident in incidents:
        if incident.get("incident_id") and incident.get("signature"):
            ordre.setdefault(incident["incident_id"], incident["signature"])
    for ligne in hypotheses:
        if ligne.get("incident_id") and ligne.get("signature"):
            ordre.setdefault(ligne["incident_id"], ligne["signature"])
    noeud_incident = {identifiant: graphe.noeud("incident", identifiant, identifiant) for identifiant in ordre}
    precedent_par_signature: dict[str, str] = {}
    for identifiant, signature in ordre.items():
        noeud_signature = graphe.noeud("signature", signature, signature)
        graphe.arete(noeud_incident[identifiant], noeud_signature, "a_pour_signature")
        if signature in precedent_par_signature:
            graphe.arete(noeud_incident[precedent_par_signature[signature]], noeud_incident[identifiant], "meme_signature")
        precedent_par_signature[signature] = identifiant

    # Hypothèses testées par chaque incident.
    testees: dict[str, set[str]] = {}
    for ligne in hypotheses:
        identifiant, texte, statut = ligne.get("incident_id"), ligne.get("texte"), ligne.get("statut")
        if identifiant not in noeud_incident or not texte or statut not in ("retenue", "refutee"):
            continue
        testees.setdefault(identifiant, set()).add(_cle_texte(texte))
        type_noeud = "hypothese_retenue" if statut == "retenue" else "hypothese_refutee"
        noeud = graphe.noeud(type_noeud, _empreinte(texte), texte)
        graphe.arete(noeud, noeud_incident[identifiant], "retenue_dans" if statut == "retenue" else "refutee_dans")

    # Souvenirs : réfutations héritées, cause, correctif et résultat.
    refutations: dict[str, list[str]] = {}
    for souvenir in souvenirs:
        textes = refutations.setdefault(souvenir["signature"], [])
        textes.extend(texte for texte in souvenir.get("hypotheses_refutees", []) if texte not in textes)
    for identifiant, signature in ordre.items():
        for texte in refutations.get(signature, []):
            if _cle_texte(texte) not in testees.get(identifiant, set()):
                noeud = graphe.noeud("hypothese_refutee", _empreinte(texte), texte)
                graphe.arete(noeud, noeud_incident[identifiant], "refutee_pour", origine="memoire")
    auteurs = {_cle_souvenir(incident["enregistrement"]): incident["incident_id"] for incident in incidents
               if isinstance(incident.get("enregistrement"), dict) and incident.get("incident_id") in noeud_incident}
    for souvenir, statut in zip(souvenirs, statuts):
        signature = souvenir["signature"]
        auteur = auteurs.get(_cle_souvenir(souvenir))
        cible = noeud_incident[auteur] if auteur else graphe.noeud("signature", signature, signature)
        cause = graphe.noeud("cause", _empreinte(souvenir["cause_confirmee"]), souvenir["cause_confirmee"])
        correctif = graphe.noeud("correctif", _empreinte(souvenir["correctif_essaye"]), souvenir["correctif_essaye"])
        graphe.arete(cause, cible, "cause_confirmee_de", date=souvenir.get("date"))
        graphe.arete(correctif, cible, "correctif_essaye_pour", date=souvenir.get("date"))
        statut = statut if statut in ("reussi", "echoue", "non_confirme") else "non_confirme"
        graphe.arete(correctif, cause, f"resultat_{statut}", date=souvenir.get("date"))
    noeuds = sorted(graphe.noeuds.values(), key=lambda noeud: (TYPES_NOEUDS.index(noeud["type"]), noeud["id"]))
    aretes = sorted(graphe.aretes.values(), key=lambda arete: (arete["source"], arete["type"], arete["cible"]))
    return {"noeuds": noeuds, "aretes": aretes}


def voisinage(graphe: dict, noeud_id: str, profondeur: int = 2) -> dict:
    """Sous-graphe des nœuds à au plus `profondeur` arêtes (sans orientation) de `noeud_id`."""
    voisins: dict[str, set[str]] = {}
    for arete in graphe["aretes"]:
        voisins.setdefault(arete["source"], set()).add(arete["cible"])
        voisins.setdefault(arete["cible"], set()).add(arete["source"])
    gardes, frontiere = {noeud_id}, {noeud_id}
    for _ in range(profondeur):
        frontiere = {voisin for noeud in frontiere for voisin in voisins.get(noeud, ())} - gardes
        gardes |= frontiere
    return {"noeuds": [noeud for noeud in graphe["noeuds"] if noeud["id"] in gardes],
            "aretes": [arete for arete in graphe["aretes"] if arete["source"] in gardes and arete["cible"] in gardes]}


def _texte_mermaid(texte: str) -> str:
    return re.sub(r'["<>{}\[\]|]', "'", texte).replace("\n", " ")


def en_mermaid(graphe: dict) -> str:
    if not graphe["noeuds"]:
        return "graph LR\n  vide[\"aucune relation\"]"
    alias = {noeud["id"]: f"n{indice}" for indice, noeud in enumerate(graphe["noeuds"])}
    lignes = ["graph LR"]
    for noeud in graphe["noeuds"]:
        ouvrant, fermant = FORMES_MERMAID[noeud["type"]]
        lignes.append(f"  {alias[noeud['id']]}{ouvrant}{noeud['type']} : {_texte_mermaid(noeud['libelle'])}{fermant}")
    for arete in graphe["aretes"]:
        lignes.append(f"  {alias[arete['source']]} -->|{arete['type']}| {alias[arete['cible']]}")
    return "\n".join(lignes)


def depuis_memoire(memoire, incidents: list[dict] | None = None) -> dict:
    """Graphe à partir d'une mémoire du moteur (JSON, Postgres ou secours) et des incidents connus."""
    from rappel.enquete.graphe import statut_souvenir

    souvenirs = memoire.lister()
    lister_hypotheses = getattr(memoire, "lister_hypotheses", None)
    hypotheses = lister_hypotheses() if callable(lister_hypotheses) else []
    statuts = [statut_souvenir(memoire, souvenir) for souvenir in souvenirs]
    return construire_graphe([souvenir.model_dump(mode="json") for souvenir in souvenirs], hypotheses,
                             incidents, statuts)
