import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from scripts import stabilite_p1, verifier_pret
from src.rappel.contrats import ContexteIncident
from src.rappel.interfaces.llm import LLMFaux


@pytest.fixture(autouse=True)
def isoler_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "faux")
    monkeypatch.delenv("LLM_FALLBACK", raising=False)
    monkeypatch.setenv("LLM_MAX_APPELS", "20")


def charger():
    chemin = Path(__file__).resolve().parents[1] / "fixtures" / "contexte_p1.json"
    return ContexteIncident.model_validate_json(chemin.read_text(encoding="utf-8"))


class LLMScripte(LLMFaux):
    def __init__(self, categories):
        super().__init__()
        self.categories = iter(categories)

    def completer(self, prompt, role="detective"):
        super().completer(prompt, role)
        categorie = next(self.categories)
        return json.dumps([{"texte": f"Cause candidate {indice}", "categorie": categorie}
                           for indice in range(3)])


def test_stabilite_scriptee_stable():
    rapport = stabilite_p1.mesurer_stabilite(charger(), LLMScripte(["schema"] * 5), mode_preuves="outils")
    assert rapport["verdict"] == "OK" and rapport["identiques"] == 5
    assert rapport["conclusion_majoritaire"][0] == "schema"


def test_stabilite_changement_deux_fois():
    rapport = stabilite_p1.mesurer_stabilite(charger(), LLMScripte(["schema"] * 3 + ["reseau"] * 2), mode_preuves="outils")
    assert rapport["verdict"] == "A CORRIGER"
    assert rapport["identiques"] == 3 and rapport["proportion"] == 0.6
    assert len(rapport["distribution"]) == 2


def test_stabilite_refus_reel(monkeypatch, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    assert stabilite_p1.main([], fabrique_llm=lambda: pytest.fail("Construction interdite")) == 2
    assert "--confirmer" in capsys.readouterr().out


def test_stabilite_cli_et_json(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "contexte_p1.json"
    chemin = tmp_path / "rapport.json"
    assert stabilite_p1.main(["--fixture", str(fixture), "--json", str(chemin)], fabrique_llm=LLMFaux) == 0
    assert json.loads(chemin.read_text())["identiques"] == 5


def test_majorite_exactement_quatre_sur_cinq():
    rapport = stabilite_p1.mesurer_stabilite(charger(), LLMScripte(["schema"] * 4 + ["ressources"]), mode_preuves="outils")
    assert rapport["verdict"] == "OK" and rapport["proportion"] == 0.8


def git_factice(chemins=(), ignore=True, env_suivi=False):
    def executer(commande, **kwargs):
        if "check-ignore" in commande:
            return CompletedProcess(commande, 0 if ignore else 1, b"", b"")
        fichiers = (".env",) if env_suivi else ()
        if "--" not in commande:
            fichiers = chemins
        stdout = b"".join(chemin.encode() + b"\0" for chemin in fichiers)
        return CompletedProcess(commande, 0, stdout, b"")
    return executer


def test_pret_variables_factices_ne_fuit_pas(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env.example").write_text("NVIDIA_API_KEY=\n")
    valeurs = []
    for nom in ("NVIDIA_API_KEY", "NVIDIA_BASE_URL", "NVIDIA_MODEL", "ANTHROPIC_API_KEY",
                "ANTHROPIC_MODEL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        valeur = "valeur-factice-" + nom.lower()
        valeurs.append(valeur)
        monkeypatch.setenv(nom, valeur)
    monkeypatch.setenv("LLM_PROVIDER", "valeur-inconnue-sensitive")
    assert verifier_pret.main([], racine=tmp_path, lancer_git=git_factice()) == 0
    sortie = capsys.readouterr().out
    assert all(valeur not in sortie for valeur in valeurs)
    assert "valeur-inconnue-sensitive" not in sortie
    assert "fournisseur non reconnu" in sortie
    assert "Demo a blanc : OK" in sortie


def test_cle_factice_suivie_detectee_sans_valeur(tmp_path, capsys):
    secret = "nvapi" + "-" + "x" * 40
    (tmp_path / ".env.example").write_text("NVIDIA_API_KEY=\n")
    (tmp_path / "suivi.txt").write_text("ligne sans secret\n" + secret + "\n")
    assert verifier_pret.main([], racine=tmp_path, lancer_git=git_factice(("suivi.txt",))) == 1
    sortie = capsys.readouterr().out
    assert "suivi.txt:2" in sortie
    assert secret not in sortie


def test_sans_cles_ne_plante_pas(tmp_path, monkeypatch, capsys):
    for nom in ("NVIDIA_API_KEY", "NVIDIA_BASE_URL", "NVIDIA_MODEL", "ANTHROPIC_API_KEY",
                "ANTHROPIC_MODEL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        monkeypatch.delenv(nom, raising=False)
    (tmp_path / ".env.example").write_text("NVIDIA_API_KEY=\n")
    assert verifier_pret.main([], racine=tmp_path, lancer_git=git_factice()) == 0
    assert "a fournir" in capsys.readouterr().out


@pytest.mark.parametrize("secret", ["sk" + "-ant-" + "z" * 32, "TOKEN=" + "A" * 32, "API_KEY=" + "B" * 32])
def test_motifs_generiques_detectes(tmp_path, secret):
    (tmp_path / "suivi.txt").write_text(secret)
    assert verifier_pret.examiner_fichiers(tmp_path, ["suivi.txt"]) == [{"fichier": "suivi.txt", "ligne": 1}]


@pytest.mark.parametrize(("ignore", "suivi"), [(False, False), (True, True)])
def test_env_non_securise_echec(tmp_path, ignore, suivi):
    (tmp_path / ".env.example").write_text("NVIDIA_API_KEY=\n")
    assert verifier_pret.main([], racine=tmp_path, lancer_git=git_factice(ignore=ignore, env_suivi=suivi)) == 1


def test_exemple_non_vide_echec_sans_fuite(tmp_path, capsys):
    (tmp_path / ".env.example").write_text("NVIDIA_API_KEY=valeur-factice-cachee\n")
    assert verifier_pret.main([], racine=tmp_path, lancer_git=git_factice()) == 1
    assert "valeur-factice-cachee" not in capsys.readouterr().out


def test_demo_erreur_echec_sans_fuite(tmp_path, capsys):
    (tmp_path / ".env.example").write_text("NVIDIA_API_KEY=\n")
    def echouer(llm):
        assert isinstance(llm, LLMFaux)
        raise RuntimeError("secret-ne-pas-divulguer")
    assert verifier_pret.main([], racine=tmp_path, lancer_git=git_factice(), demo=echouer) == 1
    assert "secret-ne-pas-divulguer" not in capsys.readouterr().out


def test_proportion_hors_cinq_repetitions():
    rapport = stabilite_p1.mesurer_stabilite(charger(), LLMScripte(["schema"] * 8 + ["reseau"] * 2), repetitions=10,
                                           mode_preuves="outils")
    assert rapport["verdict"] == "OK" and rapport["identiques"] == 8


def test_erreurs_non_comptees_comme_conclusion_stable():
    class Panne(LLMFaux):
        def completer(self, prompt, role="detective"):
            raise RuntimeError("secret-invisible")
    rapport = stabilite_p1.mesurer_stabilite(charger(), Panne())
    assert rapport["verdict"] == "A CORRIGER"
    assert rapport["identiques"] == 0 and rapport["distribution"] == []
    assert "secret-invisible" not in str(rapport)


def test_categorie_retenue_non_schema():
    donnees = charger().model_dump()
    donnees["schema"]["observe"] = list(donnees["schema"]["attendu"])
    donnees["commits_recents"] = []
    donnees["logs"] = [
        {"template": "ERROR out of memory", "compte": 50},
        {"template": "ERROR disk full", "compte": 50},
    ]
    rapport = stabilite_p1.mesurer_stabilite(donnees, LLMScripte(["ressources"] * 5), mode_preuves="outils")
    assert rapport["verdict"] == "OK"
    assert rapport["conclusion_majoritaire"][0] == "ressources"
    assert not rapport["conclusion_majoritaire"][1]


def test_fichier_non_suivi_non_scanne(tmp_path):
    secret = "nvapi" + "-" + "x" * 40
    (tmp_path / ".env.example").write_text("NVIDIA_API_KEY=\n")
    (tmp_path / "non-suivi.txt").write_text(secret)
    rapport = verifier_pret.controle_depot(tmp_path, git_factice())
    assert rapport["ok"] and rapport["trouvailles"] == []