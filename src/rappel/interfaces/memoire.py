"""Mémoire JSON locale ou Postgres, sans charger psycopg en mode léger."""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from rappel.config import RACINE, charger
from rappel.contrats import valider

_VERROU = RLock()
LOGGER = logging.getLogger(__name__)
MESSAGE_CONNEXION_POSTGRES = "Connexion à la mémoire Postgres impossible."
MESSAGE_PILOTE_POSTGRES = "Installer requirements-demo.txt pour la mémoire Postgres."
# Mode effectif observe dans le processus (pour /healthz) et alerte de bascule unique.
_ETAT_SECOURS = {"mode": None, "alerte_emise": False}
RESULTATS = ("reussi", "echoue")
COLONNES_HYPOTHESES = (
    "id", "incident_id", "signature", "texte", "statut", "preuves", "confiance", "raison_rejet", "cree_le",
)


def _verifier_resultat(resultat):
    if resultat not in RESULTATS:
        raise ValueError("Le résultat doit valoir reussi ou echoue.")


def _lignes_hypotheses(incident_id, signature, hypotheses):
    cree_le = datetime.now(timezone.utc).isoformat()
    lignes = []
    for hypothese in hypotheses:
        valider("hypothese", hypothese)
        lignes.append({
            "id": hypothese["id"], "incident_id": incident_id, "signature": signature,
            "texte": hypothese["texte"], "statut": hypothese["statut"],
            "preuves": list(hypothese["preuves"]), "confiance": hypothese["confiance"],
            "raison_rejet": hypothese["raison_rejet"], "cree_le": cree_le,
        })
    return lignes


def _sans_doublon(listes):
    return list(dict.fromkeys(texte for liste in listes for texte in liste))


class MemoireJSON:
    def __init__(self, chemin):
        chemin = Path(chemin)
        self.chemin = chemin if chemin.is_absolute() else RACINE / chemin
        # Table « hypotheses » : fichier séparé, créé seulement à la première écriture.
        self.chemin_hypotheses = self.chemin.with_name(f"{self.chemin.stem}_hypotheses.json")

    @staticmethod
    def _lire_liste(chemin):
        if not chemin.exists():
            return []
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
        if not isinstance(donnees, list):
            raise ValueError("La mémoire JSON doit contenir une liste.")
        return donnees

    def _lire(self):
        donnees = self._lire_liste(self.chemin)
        for enregistrement in donnees:
            valider("enregistrement_memoire", enregistrement)
        return donnees

    @staticmethod
    def _ecrire_liste(chemin, donnees):
        chemin.parent.mkdir(parents=True, exist_ok=True)
        temporaire = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=chemin.parent, delete=False
            ) as fichier:
                temporaire = fichier.name
                json.dump(donnees, fichier, ensure_ascii=False, indent=2)
            os.replace(temporaire, chemin)
        finally:
            if temporaire and os.path.exists(temporaire):
                os.unlink(temporaire)

    def chercher(self, signature):
        with _VERROU:
            return [element for element in self._lire() if element["signature"] == signature]

    def lister(self):
        with _VERROU:
            return self._lire()

    def ecrire(self, enregistrement):
        valider("enregistrement_memoire", enregistrement)
        with _VERROU:
            donnees = self._lire()
            donnees.append(enregistrement)
            self._ecrire_liste(self.chemin, donnees)

    def vider(self):
        with _VERROU:
            nombre = len(self._lire())
            for chemin in (self.chemin, self.chemin_hypotheses):
                if chemin.exists():
                    self._ecrire_liste(chemin, [])
            return nombre

    def hypotheses_refutees(self, signature):
        return _sans_doublon(element["hypotheses_refutees"] for element in self.chercher(signature))

    def mettre_a_jour_resultat(self, signature, resultat):
        _verifier_resultat(resultat)
        with _VERROU:
            donnees = self._lire()
            for element in reversed(donnees):
                if element["signature"] == signature:
                    element["resultat"] = resultat
                    self._ecrire_liste(self.chemin, donnees)
                    return True
            return False

    def ecrire_hypotheses(self, incident_id, signature, hypotheses):
        nouvelles = _lignes_hypotheses(incident_id, signature, hypotheses)
        with _VERROU:
            # Même identifiant d'hypothèse : la ligne est remplacée (équivalent ON CONFLICT).
            lignes = {ligne["id"]: ligne for ligne in self._lire_liste(self.chemin_hypotheses)}
            for ligne in nouvelles:
                lignes.pop(ligne["id"], None)
                lignes[ligne["id"]] = ligne
            self._ecrire_liste(self.chemin_hypotheses, list(lignes.values()))

    def lister_hypotheses(self, signature=None):
        with _VERROU:
            lignes = self._lire_liste(self.chemin_hypotheses)
        return [ligne for ligne in lignes if signature is None or ligne["signature"] == signature]


class MemoirePostgres:
    def __init__(self, database_url):
        self.database_url = database_url

    def _connecter(self):
        try:
            import psycopg
        except ImportError:
            raise RuntimeError(MESSAGE_PILOTE_POSTGRES) from None
        try:
            return psycopg.connect(self.database_url, connect_timeout=5)
        except psycopg.Error:
            raise RuntimeError(MESSAGE_CONNEXION_POSTGRES) from None

    def _enregistrements(self, requete, parametres):
        with self._connecter() as connexion, connexion.cursor() as curseur:
            curseur.execute(requete, parametres)
            noms = ("signature", "cause_confirmee", "hypotheses_refutees", "correctif_essaye", "resultat", "date")
            enregistrements = [dict(zip(noms, ligne)) for ligne in curseur.fetchall()]
            for element in enregistrements:
                element["hypotheses_refutees"] = _json(element["hypotheses_refutees"])
                element["date"] = element["date"].isoformat()
            return enregistrements

    def chercher(self, signature):
        return self._enregistrements(
            "SELECT signature, cause_confirmee, hypotheses_refutees, correctif_essaye, "
            "resultat, date FROM memoire_incidents WHERE signature = %s ORDER BY id",
            (signature,),
        )

    def lister(self):
        return self._enregistrements(
            "SELECT signature, cause_confirmee, hypotheses_refutees, correctif_essaye, "
            "resultat, date FROM memoire_incidents ORDER BY id",
            (),
        )

    def vider(self):
        with self._connecter() as connexion, connexion.cursor() as curseur:
            curseur.execute("DELETE FROM hypotheses")
            curseur.execute("DELETE FROM memoire_incidents")
            return max(curseur.rowcount, 0)

    def hypotheses_refutees(self, signature):
        with self._connecter() as connexion, connexion.cursor() as curseur:
            curseur.execute(
                "SELECT hypotheses_refutees FROM memoire_incidents WHERE signature = %s ORDER BY id",
                (signature,),
            )
            return _sans_doublon(_json(ligne[0]) for ligne in curseur.fetchall())

    def mettre_a_jour_resultat(self, signature, resultat):
        _verifier_resultat(resultat)
        with self._connecter() as connexion, connexion.cursor() as curseur:
            curseur.execute(
                "UPDATE memoire_incidents SET resultat = %s WHERE id = "
                "(SELECT max(id) FROM memoire_incidents WHERE signature = %s)",
                (resultat, signature),
            )
            return curseur.rowcount > 0

    def ecrire_hypotheses(self, incident_id, signature, hypotheses):
        lignes = _lignes_hypotheses(incident_id, signature, hypotheses)
        with self._connecter() as connexion, connexion.cursor() as curseur:
            for ligne in lignes:
                curseur.execute(
                    "INSERT INTO hypotheses "
                    "(id, incident_id, signature, texte, statut, preuves, confiance, raison_rejet, cree_le) "
                    "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET incident_id = EXCLUDED.incident_id, "
                    "signature = EXCLUDED.signature, texte = EXCLUDED.texte, statut = EXCLUDED.statut, "
                    "preuves = EXCLUDED.preuves, confiance = EXCLUDED.confiance, "
                    "raison_rejet = EXCLUDED.raison_rejet, cree_le = EXCLUDED.cree_le",
                    (
                        ligne["id"], ligne["incident_id"], ligne["signature"], ligne["texte"],
                        ligne["statut"], json.dumps(ligne["preuves"], ensure_ascii=False),
                        ligne["confiance"], ligne["raison_rejet"], ligne["cree_le"],
                    ),
                )

    def lister_hypotheses(self, signature=None):
        requete = (
            "SELECT id, incident_id, signature, texte, statut, preuves, confiance, raison_rejet, cree_le "
            "FROM hypotheses ORDER BY cree_le, id"
        )
        parametres = ()
        if signature is not None:
            requete = (
                "SELECT id, incident_id, signature, texte, statut, preuves, confiance, raison_rejet, cree_le "
                "FROM hypotheses WHERE signature = %s ORDER BY cree_le, id"
            )
            parametres = (signature,)
        with self._connecter() as connexion, connexion.cursor() as curseur:
            curseur.execute(requete, parametres)
            lignes = [dict(zip(COLONNES_HYPOTHESES, ligne)) for ligne in curseur.fetchall()]
        for ligne in lignes:
            ligne["preuves"] = _json(ligne["preuves"])
            if hasattr(ligne["cree_le"], "isoformat"):
                ligne["cree_le"] = ligne["cree_le"].isoformat()
        return lignes

    def ecrire(self, enregistrement):
        valider("enregistrement_memoire", enregistrement)
        with self._connecter() as connexion, connexion.cursor() as curseur:
            curseur.execute(
                "INSERT INTO memoire_incidents "
                "(signature, cause_confirmee, hypotheses_refutees, correctif_essaye, resultat, date) "
                "VALUES (%s, %s, %s::jsonb, %s, %s, %s)",
                (
                    enregistrement["signature"],
                    enregistrement["cause_confirmee"],
                    json.dumps(enregistrement["hypotheses_refutees"], ensure_ascii=False),
                    enregistrement["correctif_essaye"],
                    enregistrement["resultat"],
                    enregistrement["date"],
                ),
            )


class MemoireAvecSecours:
    """Postgres en principal, JSON local en secours si Postgres est injoignable.

    Chaque appel essaie d'abord le principal : le retour de Postgres est donc automatique.
    `mode` vaut "postgres" ou "fallback-json" ; les ecritures faites en secours restent dans le
    fichier JSON (aucune synchronisation automatique vers Postgres).
    """

    METHODES = ("chercher", "lister", "ecrire", "vider", "hypotheses_refutees",
                "mettre_a_jour_resultat", "ecrire_hypotheses", "lister_hypotheses")

    def __init__(self, principal, secours):
        self.principal = principal
        self.secours = secours
        self.mode = "postgres"

    def __repr__(self):
        return f"MemoireAvecSecours(mode={self.mode!r})"

    def _noter(self, mode):
        self.mode = mode
        _ETAT_SECOURS["mode"] = mode

    def _appeler(self, nom, *arguments):
        try:
            resultat = getattr(self.principal, nom)(*arguments)
        except RuntimeError as erreur:
            if str(erreur) not in (MESSAGE_CONNEXION_POSTGRES, MESSAGE_PILOTE_POSTGRES):
                raise
            if not _ETAT_SECOURS["alerte_emise"]:
                _ETAT_SECOURS["alerte_emise"] = True
                # Message fixe : jamais l'URL, l'identifiant ni le mot de passe.
                LOGGER.warning("Mémoire Postgres indisponible : bascule sur la mémoire JSON locale (fallback-json).")
            self._noter("fallback-json")
            return getattr(self.secours, nom)(*arguments)
        self._noter("postgres")
        return resultat

    def __getattr__(self, nom):
        if nom in MemoireAvecSecours.METHODES:
            return lambda *arguments: self._appeler(nom, *arguments)
        raise AttributeError(nom)


def mode_memoire_effectif():
    """Dernier mode observe pour la memoire Postgres : postgres, fallback-json ou non_verifie."""
    return _ETAT_SECOURS["mode"] or "non_verifie"


def _json(valeur):
    # psycopg décode JSONB en objets Python ; une chaîne reste possible selon l'adaptation.
    return json.loads(valeur) if isinstance(valeur, (str, bytes)) else valeur


def _memoire():
    configuration = charger()
    if configuration.memoire_provider == "json":
        return MemoireJSON(configuration.memoire_json_path)
    return MemoireAvecSecours(MemoirePostgres(configuration.database_url),
                              MemoireJSON(configuration.memoire_json_path))


def chercher(signature):
    return _memoire().chercher(signature)


def backend():
    """Backend choisi par MEMOIRE_PROVIDER (json ou postgres)."""
    return _memoire()


def ecrire(enregistrement):
    _memoire().ecrire(enregistrement)


def lister():
    return _memoire().lister()


def vider():
    return _memoire().vider()


def hypotheses_refutees(signature):
    return _memoire().hypotheses_refutees(signature)


def mettre_a_jour_resultat(signature, resultat):
    return _memoire().mettre_a_jour_resultat(signature, resultat)


def ecrire_hypotheses(incident_id, signature, hypotheses):
    _memoire().ecrire_hypotheses(incident_id, signature, hypotheses)
