import json
from pathlib import Path
import sys


RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "src"))

from rappel.contrats import (
    ContexteIncident,
    CorrectifPropose,
    EnregistrementMemoire,
    EvenementEnquete,
    Hypothese,
)


def main() -> None:
    destination = RACINE / "contrats"
    destination.mkdir(exist_ok=True)
    modeles = (
        ContexteIncident,
        Hypothese,
        EnregistrementMemoire,
        CorrectifPropose,
        EvenementEnquete,
    )
    for modele in modeles:
        chemin = destination / f"{modele.__name__}.schema.json"
        contenu = json.dumps(modele.model_json_schema(), indent=2, ensure_ascii=True)
        chemin.write_text(contenu + "\n", encoding="utf-8")
        print(chemin.relative_to(RACINE))


if __name__ == "__main__":
    main()