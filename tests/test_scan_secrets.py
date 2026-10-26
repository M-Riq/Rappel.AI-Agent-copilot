import subprocess

from scripts import scan_secrets


def depot(tmp_path):
    racine = tmp_path / "depot"
    racine.mkdir()

    def git(*arguments):
        subprocess.run(["git", "-C", str(racine), "-c", "user.email=test@example.invalid", "-c", "user.name=Test",
                        *arguments], check=True, capture_output=True)

    git("init", "-q")
    (racine / ".gitignore").write_text(".env\n", encoding="utf-8")
    (racine / ".env.example").write_text("ZETARIS_API_KEY" + "=\nLLM_PROVIDER=fake\n", encoding="utf-8")
    (racine / "lisezmoi.md").write_text("DATABASE_URL=postgresql://user:${MOT}@hote/base\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "initial")
    return racine, git


def test_depot_propre(tmp_path, capsys):
    racine, _ = depot(tmp_path)
    assert scan_secrets.main([], racine=racine) == 0
    assert "Aucun secret détecté" in capsys.readouterr().out


def test_secret_dans_l_historique_jamais_affiche(tmp_path, capsys):
    racine, git = depot(tmp_path)
    valeur = "nv" + "api" + "-" + "Q" * 30
    (racine / "config.py").write_text(f"CLE = '{valeur}'\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "ajout")
    (racine / "config.py").unlink()
    git("add", "-A")
    git("commit", "-q", "-m", "retrait")
    assert scan_secrets.main([], racine=racine) == 1
    sortie = capsys.readouterr().out
    assert "cle NVIDIA" in sortie and "config.py dans le commit" in sortie
    assert valeur not in sortie and "Q" * 10 not in sortie


def test_arbre_mot_de_passe_et_jeton(tmp_path, capsys):
    racine, _ = depot(tmp_path)
    mot = "Zx" + "9" * 10
    (racine / "notes.txt").write_text(
        f"DATABASE_URL=postgresql://rappel:{mot}@localhost/rappel\nMETERLESS_RUNTIME_TOKEN={mot}abc\n", encoding="utf-8")
    assert scan_secrets.main([], racine=racine) == 1
    sortie = capsys.readouterr().out
    assert "notes.txt:1" in sortie and "notes.txt:2" in sortie and mot not in sortie


def test_configuration_env(tmp_path, capsys):
    racine, _ = depot(tmp_path)
    (racine / ".gitignore").write_text("data/\n", encoding="utf-8")
    (racine / ".env.example").write_text("ZETARIS_API_KEY" + "=une-vraie-valeur\n", encoding="utf-8")
    assert scan_secrets.main([], racine=racine) == 1
    sortie = capsys.readouterr().out
    assert ".env absent de .gitignore" in sortie and "ZETARIS_API_KEY" in sortie
    assert "une-vraie-valeur" not in sortie


def test_substituts_ignores():
    assert scan_secrets.trouvailles_ligne("DATABASE_URL=postgresql://rappel:****@localhost/rappel") == []
    assert scan_secrets.trouvailles_ligne('URL = f"postgresql://rappel:{SECRET}@localhost"') == []
    assert scan_secrets.trouvailles_ligne("ZETARIS_API_KEY=jeton-factice-de-test") == []
    assert scan_secrets.trouvailles_ligne("ZETARIS_API_KEY=") == []


def test_marqueur_de_masquage_non_signale():
    # Le marqueur de masquage des rapports n'est pas un secret (faux positif trouvé par pret_reel).
    assert scan_secrets.est_substitut("[MASQUE]") and scan_secrets.est_substitut('[MASQUE]" in affiche')
    assert not scan_secrets.est_substitut("vraie-valeur-longue-sans-marqueur")
