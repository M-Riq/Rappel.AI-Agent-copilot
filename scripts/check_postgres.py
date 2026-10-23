"""Diagnostic de la memoire Postgres : connexion, tables de sql/init.sql, aller-retour annule.

Le mot de passe de DATABASE_URL est masque a l'affichage ; aucune exception brute n'est
affichee. L'aller-retour (ecriture, lecture, suppression) se fait dans une transaction annulee :
la base n'est pas modifiee.
"""

import importlib
from pathlib import Path
import sys
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rappel.config import charger

TABLES_ATTENDUES = ("hypotheses", "memoire_incidents")


def masquer_url(url):
    try:
        morceaux = urlsplit(url)
    except ValueError:
        return "(URL illisible)"
    if morceaux.password is None:
        return url
    hote = morceaux.hostname or ""
    if morceaux.port:
        hote += f":{morceaux.port}"
    utilisateur = f"{morceaux.username}:****@" if morceaux.username else "****@"
    return urlunsplit((morceaux.scheme, utilisateur + hote, morceaux.path, morceaux.query, morceaux.fragment))


def aller_retour(curseur):
    """Ecrit, relit puis supprime une ligne de chaque table ; True si tout est relu."""
    signature = f"diagnostic:{uuid4().hex}"
    curseur.execute(
        "INSERT INTO memoire_incidents "
        "(signature, cause_confirmee, hypotheses_refutees, correctif_essaye, resultat, date) "
        "VALUES (%s, %s, %s::jsonb, %s, %s, %s) RETURNING id",
        (signature, "diagnostic", "[]", "diagnostic", "reussi", "2026-01-01"),
    )
    identifiant = curseur.fetchone()[0]
    curseur.execute("SELECT signature FROM memoire_incidents WHERE id = %s", (identifiant,))
    incident_relu = curseur.fetchone()
    curseur.execute("DELETE FROM memoire_incidents WHERE id = %s", (identifiant,))
    hypothese = f"{signature}:hyp-1"
    curseur.execute(
        "INSERT INTO hypotheses (id, incident_id, signature, texte, statut, preuves, confiance, raison_rejet) "
        "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
        (hypothese, "diagnostic", signature, "diagnostic", "ouverte", "[]", 0.0, None),
    )
    curseur.execute("SELECT id FROM hypotheses WHERE id = %s", (hypothese,))
    hypothese_relue = curseur.fetchone()
    curseur.execute("DELETE FROM hypotheses WHERE id = %s", (hypothese,))
    return bool(incident_relu and incident_relu[0] == signature and hypothese_relue and hypothese_relue[0] == hypothese)


def main(argv=None, pilote=None):
    configuration = charger()
    print(f"DATABASE_URL : {masquer_url(configuration.database_url)}")
    try:
        psycopg = pilote or importlib.import_module("psycopg")
    except ImportError:
        print("Pilote psycopg absent : installer requirements-demo.txt (pip install -r requirements-demo.txt).")
        return 2
    try:
        connexion = psycopg.connect(configuration.database_url, connect_timeout=5)
    except Exception:
        print("Connexion Postgres impossible : verifier le service (docker compose up -d postgres) et DATABASE_URL.")
        return 1
    try:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT 1")
            if curseur.fetchone()[0] != 1:
                print("SELECT 1 : reponse inattendue.")
                return 1
            print("SELECT 1 : OK")
            curseur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name IN (%s, %s)",
                TABLES_ATTENDUES,
            )
            presentes = {ligne[0] for ligne in curseur.fetchall()}
            manquantes = [table for table in TABLES_ATTENDUES if table not in presentes]
            if manquantes:
                print("Tables absentes : " + ", ".join(manquantes) + " (appliquer sql/init.sql).")
                return 1
            print("Tables hypotheses et memoire_incidents : presentes")
            if not aller_retour(curseur):
                print("Aller-retour ecriture/lecture/suppression : ECHEC")
                return 1
            print("Aller-retour ecriture/lecture/suppression : OK (transaction annulee)")
        return 0
    except Exception:
        print("Diagnostic Postgres interrompu : requete refusee ; aucun detail sensible affiche.")
        return 1
    finally:
        try:
            connexion.rollback()
        finally:
            connexion.close()


if __name__ == "__main__":
    raise SystemExit(main())
