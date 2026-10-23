# API Rappel

Source : [api.py](../src/rappel/api.py). API locale **sans authentification** : ne pas
l'exposer sur Internet. Un seul processus Uvicorn (l'état des incidents est en mémoire).

```bash
uvicorn rappel.api:app --app-dir src        # http://127.0.0.1:8000 ; /docs, /redoc, /openapi.json
```

Modes lus à chaque requête dans `rappel.config.Configuration` (`LLM_PROVIDER`,
`SOURCE_PROVIDER`, `MEMOIRE_PROVIDER`, `SEUIL_ABSTENTION`, `MAX_TOURS_ENQUETE`,
`LLM_MAX_APPELS`). Le moteur (LangGraph) n'est importé qu'au lancement d'une enquête.

## Contrat du squelette (inchangé)

| Méthode | Route | Réponse |
| --- | --- | --- |
| GET | `/healthz` (alias `/sante`) | `{"statut": "ok", "llm", "source", "memoire"}` ; avec `MEMOIRE_PROVIDER=postgres`, clé en plus `memoire_mode_effectif` (`postgres`, `fallback-json` ou `non_verifie`, dernier mode observé, sans connexion supplémentaire) |
| POST | `/incidents` | **201** `{"id"}` ; **422** si le JSON Schema `contexte_incident` échoue ; **409** si doublon |
| GET | `/incidents/{id}/evenements` | Liste d'événements (vue d'affichage, voir ci-dessous) |
| POST | `/correctifs/{id}/decision` | `{"decision": "appliquer" \| "refuser"}` -> `{"id", "decision", "execute": false, "message"}` ; jamais d'exécution de patch |

`POST /incidents` enregistre le contexte puis lance l'enquête du moteur dans un thread
(une enquête à la fois). La réponse ne l'attend pas.

`GET /incidents/{id}/evenements` :
- mode `fake` : vue du squelette Phase 0/1. L'incident « p1 » des fixtures (soumis ou non)
  renvoie `fixtures/evenements_p1.json` avec l'identifiant demandé ; tout autre contexte
  renvoie `[]`. Ces événements de fixture ne sont jamais présentés comme une enquête réelle ;
- autre mode (`meterless`) : événements de l'enquête réelle de l'incident soumis ; la
  fixture « p1 » non soumise renvoie **501** ;
- **404** si l'incident est inconnu.

`POST /correctifs/{id}/decision` : si l'enquête a produit un correctif, la décision est
enregistrée et ajoutée au fil (événement `decision`) ; sinon, en mode fake, « p1 » vise le
correctif de fixture comme avant ; **409** si l'enquête est en cours ou sans correctif
(abstention, erreur) ; **404** si inconnu ; **422** si la décision est invalide.

## Ajouts du moteur d'enquête

| Méthode | Route | Rôle |
| --- | --- | --- |
| GET | `/incidents` | Résumés récents (`limite` 1-200, défaut 50 ; filtre `statut` : `en_cours`, `termine`, `erreur`) |
| GET | `/incidents/{id}` | Statut et résultat de l'enquête |
| GET | `/incidents/{id}/enquete/evenements?apres=N` | Fil en direct de l'enquête (étapes > N), tous modes |
| GET | `/comparaison?a=<id>&b=<id>` | Métriques de deux enquêtes terminées, réductions et règle des 60 % |
| POST | `/incidents/{id}/resultat-action` | `{"resultat": "reussi" \| "echoue"}` -> met à jour le dernier souvenir de la signature (**202**) |
| GET | `/memoire?signature=...` | Souvenirs et réfutations par signature (textes potentiellement sensibles) |
| POST | `/memoire/reinitialiser` | `{"confirmer": true, "incidents": false}` ; `confirmer` strictement booléen et vrai, sinon **422** ; **409** si une enquête est en cours |

`GET /incidents/{id}` (enquête terminée) :

```json
{
  "incident_id": "p1", "statut": "termine", "llm": "fake", "enquete_simulee": true,
  "hypotheses": [{"id": "p1:hyp-1", "statut": "retenue", "preuves": ["q1 [...] 2 ligne(s) : SELECT ..."], "...": "..."}],
  "correctif": {"description": "...", "niveau_risque": "validation", "...": "..."},
  "decision_abstention": {"abstient": false, "confiance": 1.0, "manque": ["incident similaire en mémoire"]},
  "metriques": {"nb_evenements": 21, "nb_appels_llm": 5, "nb_tours": 5, "nb_requetes_sql": 5,
                "nb_appels_rapide": 3, "nb_appels_profonde": 2, "...": "..."},
  "mode_preuves": "sql", "source_preuves": "simule-sqlite", "preuves_simulees": true,
  "memoire_mode": "json",
  "decision_humaine": null, "resultat_action": null
}
```

- `enquete_simulee` vaut `true` en mode `fake` (raisonnement du LLM faux) ;
- `source_preuves` vaut `simule-sqlite` (`SOURCE_PROVIDER=fichier`) ou `zetaris` ;
  `preuves_simulees` vaut `true` pour une source simulée : une preuve simulée n'est jamais
  présentée comme réelle ;
- `memoire_mode` : mémoire réellement utilisée par l'enquête (`json`, `postgres` ou
  `fallback-json` si Postgres était injoignable ; `null` tant que l'enquête n'est pas terminée) ;
- statut `en_cours` : seuls `incident_id`, `statut`, `llm`, `enquete_simulee`, `memoire_mode` ;
  statut `erreur` : `message` générique, jamais le détail de l'exception.

Événements (fil et vue réelle) : validés par `contrats/evenement_enquete.schema.json`,
champs `modele` (modèle réellement renvoyé par le service) et `requete_sql` (SQL exécuté)
présents seulement s'ils ont une valeur.

## GET /comparaison?a=<id>&b=<id>

Compare deux enquêtes terminées (typiquement P1 puis P2 de même signature) :

```json
{
  "a": "p1", "b": "p2",
  "metriques": {"a": {"nb_evenements": 21, "...": "..."}, "b": {"nb_evenements": 12, "...": "..."}},
  "reductions_pct": {"evenements": 42.86, "hypotheses_testees": 75.0, "appels_llm": 40.0,
                     "tokens": null, "duree": 12.5},
  "regle_60pc": {"evenements_b_sur_a": 0.5714, "appels_b_sur_a": 0.6, "respecte": true},
  "simulation": true,
  "note": "la durée n'est pas une preuve de gain avec un LLM simulé"
}
```

- Réduction = `100 × (a − b) / a`, arrondie à 2 décimales ; `null` si la valeur de `a` est nulle
  ou inconnue (les tokens valent `null` si le fournisseur ne les communique pas).
- `regle_60pc.respecte` (critère A9) : `b` a au plus 60 % des événements de `a` **et** strictement
  moins d'appels LLM.
- `simulation` vaut `true` si l'une des enquêtes a utilisé le LLM simulé.
- **404** si un incident est inconnu, **409** si l'une des enquêtes n'est pas terminée,
  **422** si `a` ou `b` manque.

## Limites

- Pas d'authentification, pas de persistance des incidents, un seul worker recommandé.
- Meterless Nightshift, Zetaris et Postgres ne sont pas testés en réel par la suite de tests.
- L'API n'exécute aucun patch ; le résultat Action est déclaratif.
