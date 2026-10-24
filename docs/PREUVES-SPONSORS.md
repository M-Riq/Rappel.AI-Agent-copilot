# Preuves d'usage des sponsors

Produites par `python scripts/collecter_preuves_sponsors.py --confirmer` (enquête P1 réelle)
dans `data/preuves_sponsors.json`, complétées par `data/recette.json` (A3, A14) et
`data/verification_machine.json`. Sans `--confirmer` ou en mode simulé, chaque sponsor est
**NON_MESURE** et l'échantillon est marqué simulé : ce n'est pas une preuve sponsor.

| Sponsor | Ce qui est prouvé | Champ | Critère REUSSI |
| --- | --- | --- | --- |
| Zetaris | Chaque preuve est une requête SQL exécutée par l'API REST | `sponsors.zetaris.requetes`, `source_preuves` | source `zetaris` et au moins une requête exécutée |
| Meterless Nightshift | Toutes les demandes au modèle passent par Nightshift, comptées par voie | `sponsors.meterless.par_voie`, `usage` | au moins un appel réel |
| NVIDIA Nemotron | Le modèle renvoyé par le service (pas celui demandé) est un Nemotron | `sponsors.nemotron.modeles_renvoyes` | tous contiennent « nemotron » ; sinon « le routage a choisi <modèle> : fixer la voie » |

## État actuel

| Sponsor | Statut | Source |
| --- | --- | --- |
| Zetaris | NON_MESURE (simulé : `simule-sqlite`) | exécution locale sans `--confirmer` |
| Meterless Nightshift | NON_MESURE (LLM simulé) | idem |
| NVIDIA Nemotron | NON_MESURE (modèles `faux-rapide`, `faux-profond`) | idem |

À remplir après l'exécution réelle (date, heure UTC, extrait JSON sans clé) :

| Sponsor | Statut réel | Date | Extrait |
| --- | --- | --- | --- |
| Zetaris | | | |
| Meterless Nightshift | | | |
| NVIDIA Nemotron | | | |

Captures conseillées pour la vidéo : le fil d'enquête avec la requête SQL sous une preuve,
le badge « Nemotron via Meterless » et le compteur P1/P2.
