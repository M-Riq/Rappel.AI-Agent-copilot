import json
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from jsonschema import ValidationError
import pytest

from rappel.config import Configuration, charger
from rappel.enquete.adaptateurs import (
    LLMBudgetEpuise, LLMEnquete, LLMFaux, LLMIndisponible, MemoireEnquete, creer_llm_enquete,
)
from rappel.enquete.modeles import EnregistrementMemoire, Hypothese
from rappel.enquete.protocoles import MARQUEUR_DONNEES, UsageLLM
from rappel.interfaces import abstention, llm, memoire
from rappel.interfaces.abstention import AbstentionSeuil


JETON = "jeton-test-factice-a-ne-pas-divulguer"


def prompt_structure(role, donnees=None):
    return f"Role: {role}. Consignes.{MARQUEUR_DONNEES}{json.dumps(donnees or {})}"


HYPOTHESE_SCHEMA = {"id": "h1", "texte": "t", "categorie": "schema", "requetes": [
    {"id": "q1", "nb_lignes": 2, "erreur": None, "colonnes": ["ecart", "nom", "type_attendu", "type_observe"],
     "lignes": [["absente", "a", "int", None], ["nouvelle", "b", None, "int"]]}]}


# --- FakeLLM du squelette et LLMEnquete ------------------------------------------------

def test_fakellm_roles_sql_json_et_modeles():
    fournisseur = llm.FakeLLM()
    hypotheses = fournisseur.completer(prompt_structure("hypotheses"), "hypotheses")
    assert hypotheses["modele"] == "faux-rapide" and hypotheses["tool_calls"] == []
    assert len(json.loads(hypotheses["texte"])) >= 2
    verdict = fournisseur.completer(prompt_structure("verdict", {"hypotheses": [HYPOTHESE_SCHEMA],
                                                                 "incident_similaire": True}), "verdict")
    assert json.loads(verdict["texte"])["verdicts"][0]["statut"] == "retenue"
    conclusion = fournisseur.completer(prompt_structure("conclusion", {"hypothese": HYPOTHESE_SCHEMA}), "conclusion")
    assert conclusion["modele"] == "faux-profond" and "renomm" in json.loads(conclusion["texte"])["cause_confirmee"]
    correctif = fournisseur.completer(prompt_structure("correctif", {
        "hypothese": HYPOTHESE_SCHEMA, "confiance": 0.7,
        "contexte": {"composant": "c", "schema_attendu": ["a:int"], "schema_observe": ["b:int"]}}), "correctif")
    assert correctif["modele"] == "faux-profond" and json.loads(correctif["texte"])["confiance"] == 0.7


@pytest.mark.parametrize("role", ["detective", "sceptique", "historien", "outil", "conclusion", "correctif"])
def test_fakellm_roles_historiques_inchanges(role):
    # Sans bloc DONNEES du moteur, la simulation d'origine est conservee pour tous les roles.
    assert llm.FakeLLM().completer("Cherche une cause", role) == {
        "texte": f"[Simulation {role}] Vérifier les preuves avant de conclure.",
        "tool_calls": [], "modele": "fake-deterministe",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def test_llm_enquete_info_usage_par_voie():
    adaptateur = LLMEnquete(llm.FakeLLM())
    texte = adaptateur.completer(prompt_structure("hypotheses"), role="hypotheses")
    assert json.loads(texte)
    assert adaptateur.derniere_reponse_info == {
        "voie": "rapide", "modele_demande": "faux-rapide", "modele_repondu": "faux-rapide",
        "tokens_entree": 0, "tokens_sortie": 0,
    }
    adaptateur.completer(prompt_structure("conclusion", {"hypothese": HYPOTHESE_SCHEMA}), role="conclusion")
    assert adaptateur.derniere_reponse_info["voie"] == "profonde"
    assert adaptateur.derniere_reponse_info["modele_repondu"] == "faux-profond"
    assert adaptateur.usage.par_voie["rapide"] == UsageLLM(0, 0, 1)
    assert adaptateur.usage.par_voie["profonde"] == UsageLLM(0, 0, 1)
    assert adaptateur.usage.nb_appels == 2


def test_plafond_llm_max_appels(monkeypatch):
    assert Configuration().llm_max_appels == 20 and charger().llm_max_appels == 20
    monkeypatch.setenv("LLM_MAX_APPELS", "2")
    adaptateur = LLMFaux()
    adaptateur.completer("x")
    adaptateur.completer("x")
    with pytest.raises(LLMBudgetEpuise, match="LLM_MAX_APPELS"):
        adaptateur.completer("x")
    assert adaptateur.usage.nb_appels == 2
    adaptateur.debut_execution()
    adaptateur.completer("x")
    with pytest.raises(ValueError, match="LLM_MAX_APPELS"):
        Configuration(llm_max_appels=0)


def reponse_http(donnees, statut=200):
    resultat = MagicMock(status_code=statut)
    resultat.json.return_value = donnees
    return resultat


def meterless(monkeypatch, donnees=None, statut=200):
    configuration = Configuration(
        llm_provider="meterless", meterless_runtime_token=JETON, meterless_model="modele-repli",
        meterless_model_rapide="voie-rapide", meterless_model_profond="voie-profonde",
    )
    appels = []

    def poster(url, **parametres):
        appels.append(parametres)
        return reponse_http(donnees if donnees is not None else {
            "model": "nemotron-effectif", "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }, statut)

    monkeypatch.setattr(llm.requests, "post", poster)
    return LLMEnquete(llm.MeterlessLLM(configuration), configuration), appels


@pytest.mark.parametrize(("role", "modele"), [
    ("hypotheses", "voie-rapide"), ("verdict", "voie-rapide"),
    ("conclusion", "voie-profonde"), ("correctif", "voie-profonde"),
])
def test_meterless_lanes_des_roles_du_moteur(monkeypatch, role, modele):
    adaptateur, appels = meterless(monkeypatch)
    assert adaptateur.completer("p", role=role) == "ok"
    assert appels[0]["json"]["model"] == modele
    assert "tools" not in appels[0]["json"]
    assert adaptateur.derniere_reponse_info == {
        "voie": "profonde" if role in ("conclusion", "correctif") else "rapide",
        "modele_demande": modele, "modele_repondu": "nemotron-effectif",
        "tokens_entree": 5, "tokens_sortie": 3,
    }


def test_meterless_modele_repondu_reel_sans_repli(monkeypatch):
    # Le dict du squelette se replie sur le modele demande ; l'adaptateur garde la vraie valeur.
    adaptateur, _ = meterless(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})
    adaptateur.completer("p", role="hypotheses")
    assert adaptateur.derniere_reponse_info["modele_repondu"] is None
    assert adaptateur.derniere_reponse_info["modele_demande"] == "voie-rapide"
    assert adaptateur.usage.par_voie["rapide"].tokens_entree is None


def test_meterless_tools_transmis(monkeypatch):
    adaptateur, appels = meterless(monkeypatch)
    outils = [{"type": "function", "function": {"name": "run_sql"}}]
    adaptateur.completer("p", role="verdict", tools=outils)
    assert appels[0]["json"]["tools"] == outils


def test_meterless_erreur_sans_fuite(monkeypatch, caplog):
    adaptateur, _ = meterless(monkeypatch, {"error": JETON}, statut=500)
    reponse = reponse_http({}, 500)
    reponse.raise_for_status.side_effect = llm.requests.HTTPError("Authorization: Bearer " + JETON)
    monkeypatch.setattr(llm.requests, "post", MagicMock(return_value=reponse))
    with pytest.raises(LLMIndisponible) as erreur:
        adaptateur.completer("p", role="conclusion")
    textes = [str(erreur.value), repr(erreur.value), repr(adaptateur), repr(adaptateur.usage),
              str(adaptateur.derniere_reponse_info), caplog.text]
    assert all(JETON not in texte and "Bearer" not in texte for texte in textes)
    assert adaptateur.usage.par_voie["profonde"].nb_appels == 1


def test_creer_llm_enquete_selon_configuration(monkeypatch):
    assert isinstance(creer_llm_enquete(), LLMFaux)
    monkeypatch.setenv("LLM_PROVIDER", "meterless")
    adaptateur = creer_llm_enquete()
    assert type(adaptateur) is LLMEnquete and isinstance(adaptateur.fournisseur, llm.MeterlessLLM)


# --- Memoire ----------------------------------------------------------------------------

def enregistrement(**changements):
    return {"signature": "sig:comp", "cause_confirmee": "Cause", "hypotheses_refutees": ["A", "B"],
            "correctif_essaye": "Correctif", "resultat": "reussi", "date": "2026-10-22", **changements}


def hypothese(**changements):
    return {"id": "p1:hyp-1", "incident_id": "p1", "texte": "Cause", "statut": "refutee", "preuves": ["q1"],
            "confiance": 0.0, "raison_rejet": "aucune preuve SQL", **changements}


def test_memoire_json_nouvelles_methodes(tmp_path):
    backend = memoire.MemoireJSON(tmp_path / "memoire.json")
    assert backend.lister() == [] and backend.vider() == 0 and not backend.chemin.exists()
    backend.ecrire(enregistrement())
    backend.ecrire(enregistrement(hypotheses_refutees=["B", "C"], date="2026-10-23"))
    backend.ecrire(enregistrement(signature="autre", hypotheses_refutees=["Z"]))
    assert backend.hypotheses_refutees("sig:comp") == ["A", "B", "C"]
    assert backend.mettre_a_jour_resultat("sig:comp", "echoue")
    assert [element["resultat"] for element in backend.chercher("sig:comp")] == ["reussi", "echoue"]
    assert not backend.mettre_a_jour_resultat("inconnue", "echoue")
    with pytest.raises(ValueError):
        backend.mettre_a_jour_resultat("sig:comp", "autre")
    backend.ecrire_hypotheses("p1", "sig:comp", [hypothese()])
    backend.ecrire_hypotheses("p1", "sig:comp", [hypothese(statut="retenue", raison_rejet=None, confiance=0.9)])
    lignes = backend.lister_hypotheses("sig:comp")
    assert len(lignes) == 1 and lignes[0]["statut"] == "retenue"
    assert set(lignes[0]) == {"id", "incident_id", "signature", "texte", "statut", "preuves", "confiance",
                              "raison_rejet", "cree_le"}
    with pytest.raises(ValidationError):
        backend.ecrire_hypotheses("p1", "sig:comp", [hypothese(statut="inconnu")])
    assert len(backend.lister()) == 3
    assert backend.vider() == 3
    assert backend.lister() == [] and backend.lister_hypotheses() == []


def test_fonctions_module_memoire(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOIRE_JSON_PATH", str(tmp_path / "memoire.json"))
    memoire.ecrire(enregistrement())
    memoire.ecrire_hypotheses("p1", "sig:comp", [hypothese()])
    assert memoire.lister() == [enregistrement()]
    assert memoire.hypotheses_refutees("sig:comp") == ["A", "B"]
    assert memoire.mettre_a_jour_resultat("sig:comp", "echoue")
    assert memoire.vider() == 1


class CurseurFaux:
    def __init__(self, resultats, rowcount):
        self.resultats = list(resultats)
        self.rowcount = rowcount
        self.requetes = []

    def __enter__(self):
        return self

    def __exit__(self, *erreur):
        return False

    def execute(self, sql, parametres=()):
        self.requetes.append((sql, parametres))

    def fetchall(self):
        return self.resultats.pop(0) if self.resultats else []


class ConnexionFausse:
    def __init__(self, curseur):
        self.curseur = curseur

    def __enter__(self):
        return self

    def __exit__(self, *erreur):
        return False

    def cursor(self):
        return self.curseur


def pilote_faux(monkeypatch, resultats=(), rowcount=1):
    curseur = CurseurFaux(resultats, rowcount)
    connecter = MagicMock(return_value=ConnexionFausse(curseur))
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connecter, Error=RuntimeError))
    monkeypatch.setenv("MEMOIRE_PROVIDER", "postgres")
    return curseur, connecter


def verifier_parametree(curseur, *valeurs):
    for sql, _ in curseur.requetes:
        assert all(str(valeur) not in sql for valeur in valeurs), sql


def test_postgres_hypotheses_refutees_et_mise_a_jour(monkeypatch):
    curseur, _ = pilote_faux(monkeypatch, [[(["A", "B"],), ('["B", "C"]',)]], rowcount=1)
    assert memoire.hypotheses_refutees("sig:comp") == ["A", "B", "C"]
    assert curseur.requetes[-1] == (
        "SELECT hypotheses_refutees FROM memoire_incidents WHERE signature = %s ORDER BY id", ("sig:comp",))
    assert memoire.mettre_a_jour_resultat("sig:comp", "echoue") is True
    sql, parametres = curseur.requetes[-1]
    assert sql.startswith("UPDATE memoire_incidents SET resultat = %s WHERE id = ")
    assert parametres == ("echoue", "sig:comp")
    curseur.rowcount = 0
    assert memoire.mettre_a_jour_resultat("inconnue", "reussi") is False
    verifier_parametree(curseur, "sig:comp", "echoue", "inconnue")


def test_postgres_resultat_invalide_sans_connexion(monkeypatch):
    _, connecter = pilote_faux(monkeypatch)
    with pytest.raises(ValueError):
        memoire.mettre_a_jour_resultat("sig:comp", "autre")
    with pytest.raises(ValidationError):
        memoire.ecrire_hypotheses("p1", "sig:comp", [hypothese(confiance=2)])
    connecter.assert_not_called()


def test_postgres_ecrire_hypotheses_upsert_parametre(monkeypatch):
    curseur, _ = pilote_faux(monkeypatch)
    memoire.ecrire_hypotheses("p1", "sig:comp", [hypothese(), hypothese(id="p1:hyp-2", statut="ouverte",
                                                                        raison_rejet=None)])
    assert len(curseur.requetes) == 2
    sql, parametres = curseur.requetes[0]
    assert sql.startswith("INSERT INTO hypotheses (id, incident_id, signature, texte, statut, preuves, "
                          "confiance, raison_rejet, cree_le) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)")
    assert "ON CONFLICT (id) DO UPDATE" in sql
    assert parametres[:5] == ("p1:hyp-1", "p1", "sig:comp", "Cause", "refutee")
    assert json.loads(parametres[5]) == ["q1"] and parametres[6:8] == (0.0, "aucune preuve SQL")
    datetime.fromisoformat(parametres[8])
    verifier_parametree(curseur, "p1:hyp-1", "sig:comp", "aucune preuve SQL")


def test_postgres_lister_vider_et_lister_hypotheses(monkeypatch):
    ligne = ("sig:comp", "Cause", ["A"], "Correctif", "reussi", date(2026, 10, 22))
    cree_le = datetime(2026, 10, 22, 10, tzinfo=timezone.utc)
    hypothese_sql = ("p1:hyp-1", "p1", "sig:comp", "Cause", "refutee", '["q1"]', 0.0, "aucune preuve SQL", cree_le)
    curseur, _ = pilote_faux(monkeypatch, [[ligne], [hypothese_sql]], rowcount=3)
    assert memoire.lister() == [enregistrement(hypotheses_refutees=["A"])]
    assert curseur.requetes[-1][1] == ()
    lignes = memoire.backend().lister_hypotheses("sig:comp")
    assert lignes[0]["preuves"] == ["q1"] and lignes[0]["cree_le"] == cree_le.isoformat()
    assert curseur.requetes[-1][1] == ("sig:comp",)
    assert memoire.vider() == 3
    assert [sql for sql, _ in curseur.requetes[-2:]] == ["DELETE FROM hypotheses", "DELETE FROM memoire_incidents"]


def test_memoire_enquete_pydantic_sur_postgres(monkeypatch):
    curseur, _ = pilote_faux(monkeypatch, [[("sig:comp", "Cause", ["A"], "Correctif", "reussi", date(2026, 10, 22))]])
    adaptateur = MemoireEnquete()
    assert isinstance(adaptateur.backend, memoire.MemoirePostgres)
    assert adaptateur.chercher("sig:comp") == [EnregistrementMemoire(**enregistrement(hypotheses_refutees=["A"]))]
    adaptateur.ecrire(EnregistrementMemoire(**enregistrement()))
    assert "VALUES (%s, %s, %s::jsonb" in curseur.requetes[-1][0]
    adaptateur.ecrire_hypotheses("p1", "sig:comp", [Hypothese(**hypothese())])
    assert curseur.requetes[-1][1][0] == "p1:hyp-1"
    assert repr(adaptateur) == "MemoireEnquete(backend=MemoirePostgres)"


def test_memoire_json_ne_charge_pas_psycopg(tmp_path):
    adaptateur = MemoireEnquete.json(tmp_path / "memoire.json")
    adaptateur.ecrire(EnregistrementMemoire(**enregistrement()))
    assert adaptateur.chercher("sig:comp")[0].date == "2026-10-22"
    assert "psycopg" not in sys.modules


# --- Abstention : un seul seuil --------------------------------------------------------

@pytest.mark.parametrize("seuil", ["0.6", "0.9"])
def test_accord_des_deux_abstentions_a_la_limite(monkeypatch, seuil):
    monkeypatch.setenv("SEUIL_ABSTENTION", seuil)
    valeur = float(seuil)
    politique = AbstentionSeuil()
    assert politique.seuil == valeur == charger().seuil_abstention
    for confiance, attendu in ((valeur, False), (valeur - 1e-9, True)):
        modele = Hypothese(id="h", incident_id="p1", texte="t", statut="retenue", preuves=["q1"],
                           confiance=confiance, raison_rejet=None)
        squelette = abstention.decider({"confiance": confiance}, {"incident_id": "p1"})["abstention"]
        # Sans bonus (une source, pas de memoire), les deux politiques comparent la meme valeur.
        moteur = politique.decider(modele, nb_sources=1, incident_similaire=False).abstient
        assert squelette is moteur is attendu


def test_message_fournisseur_avec_entete_masque():
    class FournisseurBavard:
        def completer(self, prompt, role, tools=None):
            raise RuntimeError("echec ; Authorization: Bearer " + JETON)

    with pytest.raises(LLMIndisponible) as erreur:
        LLMEnquete(FournisseurBavard()).completer("p", role="hypotheses")
    assert str(erreur.value) == "Fournisseur LLM indisponible."
