# Dossier de soumission — Rappel

Ce document prépare le dépôt. **Tous les chiffres viennent de `data/recette.json` et
`data/recette_globale.json`, produits en mode simulé** (`LLM_PROVIDER=fake`,
`SOURCE_PROVIDER=fichier`, mémoire JSON). Ils doivent être remplacés par ceux d'une exécution
réelle (`--confirmer`) avant d'être présentés comme une mesure sponsor.

## 1. Calendrier (heures UTC)

| Date | Étape |
| --- | --- |
| 22 oct. 2026 | Début du build : squelette, contrats, moteur intégré |
| 23–24 oct. | Modules A/B/C, recettes automatiques, interface |
| 25 oct. | Machine de démo : Zetaris, Postgres, Nightshift en réel ; recette `--confirmer` |
| 26 oct. | **Gel du code** : plus de fonctionnalité, seulement corrections et documents |
| 27 oct. | **Dépôt avant 23:45 UTC** (viser 20:00 UTC pour garder une marge) |

## 2. Liste de contrôle du dépôt

- [ ] `pytest --ignore=tests/test_ui.py` vert, puis `pytest` complet sur une machine avec Streamlit
- [ ] `python scripts/scan_secrets.py` : aucun secret (historique et arbre de travail)
- [ ] `python scripts/recette_module_a.py --confirmer` : aucune case ECHOUE, A14 mesurée
- [ ] `python scripts/recette_globale.py --confirmer` : G1–G4 en réel
- [ ] `python scripts/collecter_preuves_sponsors.py --confirmer` : preuves réelles par sponsor
- [ ] `python scripts/pret_reel.py --confirmer` : GO (procédure : `docs/RUNBOOK-BRANCHEMENT.md`)
- [ ] `python scripts/recette_reelle.py --confirmer` : toutes les étapes réelles en une commande
- [ ] Rappel : les tests contre `tests/faux_services/` (faux Nightshift, Zetaris, Postgres locaux) ne sont PAS des preuves sponsor
- [ ] Vidéo 1–4 min enregistrée, sous-titrée, lien public vérifié en navigation privée
- [ ] Diapositives exportées en PDF
- [ ] README à jour (bloc « Démarrage rapide » repris de `docs/PROPOSITION-README.md`)
- [ ] Dépôt public, licence, `.env` absent, `.env.example` sans valeur secrète
- [ ] Réponses de l'organisation aux questions de la section 7 reportées ici
- [ ] Formulaire de dépôt rempli, accusé de réception conservé

## 3. Plan des diapositives

1. **Problème** — Un pipeline de données casse la nuit ; l'astreinte relit des milliers de
   lignes de journaux et reteste les mêmes pistes que la dernière fois.
2. **Solution** — Rappel enquête comme un détective : au moins deux hypothèses, chacune vérifiée
   par une requête SQL réellement exécutée, puis un correctif à risque mesuré ou une abstention
   motivée. Il se souvient des pistes réfutées.
3. **Démo P1 / P2 / P3** — P1 : colonne renommée, cause trouvée et correctif appliqué avec
   sauvegarde. P2 : incident voisin, la mémoire évite les pistes déjà réfutées. P3 : preuves
   insuffisantes, Rappel s'abstient et liste ce qui manque.
4. **Architecture et sponsors** — Zetaris (preuves SQL par API REST), Meterless Nightshift
   (toutes les demandes au modèle), NVIDIA Nemotron (voie rapide pour les hypothèses, voie
   profonde pour la conclusion et le correctif).
5. **Gain chiffré** — P2 utilise 12 événements contre 21 pour P1 (−43 %) et 3 appels au modèle
   contre 5 (−40 %) ; P1 rejouée 5 fois : 5 conclusions identiques. *Mesures simulées : à
   remplacer par les mesures réelles.*
5 bis. **War room et graphe de relations** — Un avocat du diable conteste la cause retenue ;
   le détective doit citer une preuve existante, le code arbitre et borne le débat (recette W1 :
   1 tour pour un plafond de 1, 2 appels pour un plafond de 6 ; W2 : preuves identiques avec et
   sans débat ; W3 : arrêt « plafond de tours atteint »). Le graphe relie P1 et P2 (même
   signature, hypothèses réfutées héritées) sans LLM. *Mesures simulées.*
6. **Impact** — Moins de temps d'astreinte, aucune action irréversible sans validation, chaque
   conclusion traçable jusqu'à sa requête.
7. **Suite** — Plus de sources Zetaris, calibration du seuil sur des incidents réels, mémoire
   partagée entre équipes.

## 4. Script de la vidéo (1 à 4 minutes)

| Minute | Image | Texte dit |
| --- | --- | --- |
| 0:00–1:00 | Pipeline rouge, journaux bruts | « Cette nuit, le pipeline orders_etl a cassé. Voici ce que voit l'astreinte. Rappel, lui, résume les journaux et propose deux hypothèses, chacune avec sa requête SQL. » |
| 1:00–2:00 | Fil d'enquête P1, requêtes et résultats | « Chaque preuve est une requête envoyée à Zetaris ; le code, pas le modèle, décide si une hypothèse est retenue. Cause : colonne renommée. Le rayon d'impact est faible : correctif appliqué avec sauvegarde, pipeline vert. » |
| 2:00–3:00 | P2 puis compteur P1/P2 | « Nouvel incident voisin : Rappel retrouve P1 et ne reteste pas les pistes réfutées. Moins d'événements, moins d'appels au modèle. » |
| 2:30–3:00 | War room puis zone « Relations » | « Avant d'agir, un avocat du diable conteste la cause ; le détective doit citer une preuve déjà exécutée, le code arbitre et le débat s'arrête au plafond. Le graphe montre que P2 partage la signature de P1 et hérite de ses pistes réfutées. » |
| 3:00–4:00 | P3, abstention, badges modèle | « Ici les preuves manquent : Rappel s'abstient et dit quoi chercher. Toutes les demandes passent par Meterless Nightshift vers Nemotron ; le modèle affiché est celui renvoyé par le service. » |

Si une partie est filmée en mode simulé, l'écran doit montrer le badge « simulé ».

## 5. Texte d'usage des sponsors

- **Zetaris** — Rappel n'affirme rien sans preuve : chaque vérification est une requête SQL en
  lecture seule (une instruction, tables en liste blanche, 50 lignes au plus) envoyée par l'API
  REST de Zetaris via l'outil `run_sql`. La requête et son résultat sont enregistrés dans
  l'événement d'enquête.
- **Meterless Nightshift** — Toutes les demandes au modèle passent par Nightshift (API
  compatible OpenAI), avec deux voies configurables et un plafond d'appels par exécution.
- **NVIDIA Nemotron** — Voie rapide (Nemotron Lightning) pour proposer et juger les hypothèses ;
  voie profonde (Nemotron Super) pour la conclusion et le correctif. Le champ `modele` de chaque
  événement contient le modèle réellement renvoyé par le service.

## 6. Preuves par sponsor

| Sponsor | Preuve attendue | Où la trouver | État |
| --- | --- | --- | --- |
| Zetaris | `requete_sql` des événements de type preuve, source `zetaris` | `GET /incidents/{id}/enquete/evenements`, `data/preuves_sponsors.json` | NON_MESURE (simulé : `simule-sqlite`) |
| Meterless Nightshift | Appels comptés par voie, réponse du service | `scripts/check_meterless.py --confirmer`, `data/preuves_sponsors.json` | NON_MESURE (LLM simulé) |
| NVIDIA Nemotron | `modele` des événements contient « nemotron » (case A14) | `data/recette.json` après `--confirmer` | NON_MESURE |

## 7. Questions de règlement pour Riq

| Question | Réponse de l'organisation |
| --- | --- |
| La vidéo peut-elle montrer une partie en mode simulé si elle est signalée ? | |
| Faut-il que le dépôt soit public au moment du dépôt ou à la fin du jugement ? | |
| L'usage de Zetaris doit-il passer par une instance fournie par l'organisation ? | |
| Le code écrit avant le 22 octobre (moteur d'enquête) est-il admis s'il est documenté ? | |
| Durée maximale exacte de la vidéo et format attendu (lien, fichier) ? | |
| Nombre de diapositives imposé ? | |
| Les jetons Nightshift fournis ont-ils un quota à ne pas dépasser pendant la démo ? | |

## 8. Chiffres disponibles (simulés)

| Mesure | Valeur | Source |
| --- | --- | --- |
| Cases A1–A15 sans ECHOUE | 14 REUSSI, A14 NON_MESURE | `data/recette.json` |
| Parties réelles non mesurées | A3, A7, A14, A15 | `data/recette.json` |
| P2 / P1 événements | 12 / 21 | `data/recette.json`, `data/recette_globale.json` |
| P2 / P1 appels LLM | 3 / 5 | idem |
| P1 rejouée 5 fois | 5 / 5 conclusions identiques | `data/recette.json` (A15, simulé) |
| Cycles complets G4 | 5 / 5 | `data/recette_globale.json` |
| War room W1-W3 | 3 REUSSI (simulé) : 1 tour / plafond 1, 2 appels / plafond 6, arrêt « plafond de tours atteint » | `data/recette.json` (`cases_war_room`) |
| Recette réelle | 8 étapes NON_MESURE sans clés (Nightshift, Zetaris, Postgres, A14, A15, boucle, war room, sponsors) | `data/recette_reelle.json` |
