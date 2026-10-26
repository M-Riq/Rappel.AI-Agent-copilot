"""Migration idempotente JSON/Postgres vers H-MEM sans suppression de la source."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import sys

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "src"))

from rappel.config import charger
from rappel.interfaces.memoire import MemoireJSON, MemoirePostgres
from rappel.hmem import HMEM


def empreinte(valeur):
    texte = json.dumps(valeur, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(texte.encode("utf-8")).hexdigest()[:24]


def donnees_legacy(valeur):
    return valeur.model_dump(mode="json") if hasattr(valeur, "model_dump") else dict(valeur)


def charger_source(args, configuration):
    url = args.database_url or os.environ.get("DATABASE_URL")
    if url:
        source = MemoirePostgres(url)
        type_source = "postgres"
    else:
        source = MemoireJSON(args.source_json or configuration.memoire_json_path)
        type_source = "json"
    incidents = [donnees_legacy(record) for record in source.lister()]
    lire_hypotheses = getattr(source, "lister_hypotheses", None)
    hypotheses = lire_hypotheses() if callable(lire_hypotheses) else []
    return type_source, incidents, hypotheses


def sources_existantes(chemin):
    if not chemin.exists():
        return set()
    try:
        connexion = sqlite3.connect(f"file:{chemin.as_posix()}?mode=ro", uri=True, timeout=2)
        try:
            rows = connexion.execute(
                "SELECT donnees FROM memoires_hmem"
            ).fetchall()
        finally:
            connexion.close()
        return {
            json.loads(row[0])["source"] for row in rows
        }
    except (OSError, sqlite3.Error, ValueError, KeyError):
        raise RuntimeError("Lecture de la destination H-MEM impossible.") from None


def migrer(incidents, hypotheses, destination, simuler=False):
    presents = sources_existantes(destination)
    elements = []
    for record in incidents:
        identifiant = f"migration:incident:{empreinte(record)}"
        elements.append((identifiant, {
            "content": "Enregistrement historique Rappel (non revalidé) : "
                       + json.dumps(record, ensure_ascii=False, sort_keys=True),
            "type": "general", "layer": "working", "confidence": 0.3,
            "source": identifiant,
            "tags": ["migration:legacy_record", "non_verifie:preuves_sql_absentes"],
            "provenance": {
                "origin": "migration", "learnedAt": 0,
                "label": "migration depuis la mémoire Rappel ; SQL historique non certifié",
                "refs": ["legacy:enregistrement", f"empreinte:{empreinte(record)}"],
            },
        }))
    for hypothesis in hypotheses:
        ligne = donnees_legacy(hypothesis)
        identifiant = f"migration:hypothese:{empreinte(ligne)}"
        elements.append((identifiant, {
            "content": "Hypothèse historique Rappel (non revalidée) : "
                       + json.dumps(ligne, ensure_ascii=False, sort_keys=True),
            "type": "general", "layer": "working", "confidence": 0.2,
            "source": identifiant, "tags": ["migration:legacy_hypothesis"],
            "provenance": {
                "origin": "migration", "learnedAt": 0,
                "label": "migration d'hypothèse ; ne constitue pas un fait confirmé",
                "refs": ["legacy:hypothese", f"empreinte:{empreinte(ligne)}"],
            },
        }))
    a_migrer = [(source, item) for source, item in elements if source not in presents]
    rapport = {
        "destination": str(destination),
        "incidents_source": len(incidents),
        "hypotheses_source": len(hypotheses),
        "deja_presentes": len(elements) - len(a_migrer),
        "a_migrer": len(a_migrer),
        "simuler": simuler,
        "source_conservee": True,
        "incidents_importes_comme_faits_confirmes": 0,
    }
    if simuler or not a_migrer:
        return rapport
    engine = HMEM(persist_path=destination, actor="migration")
    try:
        for _, item in a_migrer:
            engine.add(item)
        rapport["importes"] = len(a_migrer)
    finally:
        engine.close()
    return rapport


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-json", type=Path)
    parser.add_argument("--database-url", help="Importer Postgres ; valeur lue depuis l'environnement si absente.")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--simuler", action="store_true", help="Rapport sans création ni modification H-MEM.")
    parser.add_argument("--rapport", type=Path)
    args = parser.parse_args(argv)
    try:
        configuration = charger()
        destination = args.destination or Path(configuration.hmem_path)
        if not destination.is_absolute():
            destination = RACINE / destination
        type_source, incidents, hypotheses = charger_source(args, configuration)
        rapport = migrer(incidents, hypotheses, destination, args.simuler)
        rapport["source"] = type_source
        texte = json.dumps(rapport, ensure_ascii=False, indent=2) + "\n"
        if args.rapport:
            args.rapport.parent.mkdir(parents=True, exist_ok=True)
            args.rapport.write_text(texte, encoding="utf-8")
        print(texte, end="")
        return 0
    except Exception:
        print("Migration impossible : vérifier la source et les droits du fichier H-MEM.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
