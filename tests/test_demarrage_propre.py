"""Parcours de démarrage propre : lecture du bloc, classement des commandes, environnement."""

from pathlib import Path

from scripts import test_demarrage_propre as demarrage

RACINE = Path(__file__).resolve().parents[1]


def test_bloc_lu_dans_la_proposition_de_readme():
    texte = (RACINE / "docs" / "PROPOSITION-README.md").read_text(encoding="utf-8")
    commandes, indice = demarrage.lire_bloc_demarrage(["", texte])
    assert indice == 1
    assert 1 <= len(commandes) <= 10
    natures = [demarrage.classer(commande) for commande in demarrage.decouper(commandes)]
    assert "inconnue" not in natures
    assert {"clone", "venv", "pip", "copie", "pytest", "api"} <= set(natures)


def test_section_absente():
    assert demarrage.lire_bloc_demarrage(["# Titre\n\n```bash\nls\n```\n"]) == ([], -1)


def test_decoupage_et_commentaires():
    texte = "## Démarrage rapide\n\n```bash\n# commentaire\ngit clone x r && cd r\npytest -q   # tests\n```\n## Suite\n"
    commandes, _ = demarrage.lire_bloc_demarrage([texte])
    assert commandes == ["git clone x r && cd r", "pytest -q"]
    assert demarrage.decouper(commandes) == ["git clone x r", "cd r", "pytest -q"]


def test_paquets_requis_sans_versions():
    paquets = demarrage.paquets_requis(RACINE / "requirements.txt")
    assert "fastapi" in paquets and "python-dotenv" in paquets
    assert all("=" not in paquet for paquet in paquets)


def test_environnement_propre_sans_configuration_locale(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "meterless")
    monkeypatch.setenv("ZETARIS_API_KEY", "valeur-factice")
    monkeypatch.setenv("MEMOIRE_JSON_PATH", "ailleurs.json")
    env = demarrage.env_propre(tmp_path / ".venv")
    assert not {"LLM_PROVIDER", "ZETARIS_API_KEY", "MEMOIRE_JSON_PATH"} & set(env)
    assert env["VIRTUAL_ENV"] == str(tmp_path / ".venv")
    assert env["PATH"].startswith(str(tmp_path / ".venv"))


def test_fixtures_generees_en_lf_sur_tout_clone():
    attributs = (RACINE / ".gitattributes").read_text(encoding="utf-8")
    assert "fixtures/moteur/*.json text eol=lf" in attributs
