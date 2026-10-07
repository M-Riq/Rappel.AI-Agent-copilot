# API Rappel

## Usage local et lancement

Cette API de hackathon ne comporte **aucune authentification**. Elle ne doit pas
etre exposee sur Internet ou un reseau non fiable. Lancer depuis la racine du
depot, dans Git Bash, avec le venv Python 3.11.9 :

```bash
./.venv/Scripts/python.exe -m uvicorn src.rappel.api:app --host 127.0.0.1 --port 8000
```

Adresse : http://127.0.0.1:8000

- Documentation interactive Swagger : http://127.0.0.1:8000/docs
- Documentation ReDoc : http://127.0.0.1:8000/redoc
- Schema OpenAPI : http://127.0.0.1:8000/openapi.json

L'instance `app` utilise `creer_llm()` (configuration `.env`), `MemoireJSON()`
et `AbstentionSeuil()`. Sans `LLM_PROVIDER`, le fournisseur est `faux`.
Aucun appel LLM n'est fait lors de la creation de l'application.

Les incidents et decisions sont stockes dans un dictionnaire protege par un
verrou, sans persistance. Un redemarrage les efface. Utiliser **un seul processus**
Uvicorn : plusieurs workers auraient des dictionnaires distincts.
La memoire d'incidents reste, elle, dans `data/memoire.json`, ignore par Git.
Les enquetes sont executees dans un thread unique pour isoler les budgets LLM et
les ecritures de memoire partagees ; plusieurs soumissions sont mises en file.
Le statut `en_cours` couvre aussi cette attente. Les lectures restent accessibles.

## Tableau des routes

| Methode | Route | Role |
|---|---|---|
| POST | `/incidents` | Soumettre une enquete |
| GET | `/incidents` | Resumes de session, sans contexte ni textes |
| GET | `/incidents/{id}` | Resultat d'une enquete |
| GET | `/incidents/{id}/evenements` | Lecture progressive |
| POST | `/correctifs/{id}/decision` | Decision humaine |
| POST | `/incidents/{id}/resultat-action` | Retour Action |
| GET | `/memoire` | Souvenirs et refutations |
| POST | `/memoire/reinitialiser` | Effacement destructif confirme |
| GET | `/sante` | Sante de l'API |

## GET /incidents

Reponse **200** : liste vide `[]` au depart, sinon resumes dans l'ordre inverse
de creation dans la session (le dernier soumis d'abord, meme en cas de dates egales).
`cree_le` est la date UTC ISO de reception, pas l'horodatage du contexte.
`limite` vaut 50, entre 1 et 200 ; `statut` est facultatif et accepte seulement
`en_cours`, `termine`, `erreur`. Parametre invalide : **422**. Le filtre precede la limite.

```json
[
  {
    "incident_id": "inc-demo",
    "signature": "schema_mismatch:pipeline_demo",
    "statut": "termine",
    "cree_le": "2026-10-07T10:00:00+00:00",
    "nb_evenements": 13,
    "a_correctif": true,
    "abstention": false,
    "decision_humaine": null,
    "resultat_action": null
  }
]
```

`abstention` vaut `null` avant la fin ou en erreur. `resultat_action` reste `null`
jusqu'au retour Action, sans recopier le resultat provisoire de la memoire.
Les decisions humaines et retours Action augmentent `nb_evenements` et mettent
a jour leurs champs. Aucun log, commit, contexte brut ou texte d'evenement dans
ces resumes. Exemple filtre : `GET /incidents?limite=10&statut=termine`.

## GET /memoire

**Usage local uniquement : cette route expose des textes potentiellement sensibles**
(causes, correctifs, refutations, extraits de preuves). L'API n'a pas d'authentification.
Reponse **200**, ordre d'ecriture conserve. `signature` filtre par egalite exacte.
Une signature inconnue ou une memoire vide renvoie :

```json
{"souvenirs": [], "refutations_par_signature": {}}
```

Exemple `GET /memoire?signature=schema_mismatch:pipeline_demo` :

```json
{
  "souvenirs": [
    {
      "signature": "schema_mismatch:pipeline_demo",
      "cause_confirmee": "colonne renommee probable",
      "hypotheses_refutees": ["Saturation des ressources", "Probleme reseau ou timeout"],
      "correctif_essaye": "Restaurer le schema attendu",
      "resultat": "reussi",
      "date": "2026-10-07T10:00:00+00:00"
    }
  ],
  "refutations_par_signature": {
    "schema_mismatch:pipeline_demo": ["Saturation des ressources", "Probleme reseau ou timeout"]
  }
}
```

Fusion des refutations sans doublon, dans l'ordre de leur premiere apparition.
Le souvenir contient tous les champs du contrat. Les valeurs ci-dessus sont
illustratives ; les donnees reelles viennent de la memoire injectee.
Lecture serialisee avec les ecritures d'enquete ; elle peut attendre la fin d'une
enquete utilisant les dependances partagees. Erreur de stockage : **500** generique.

## POST /memoire/reinitialiser

**Route destructive, locale uniquement, sans authentification : les souvenirs sont
supprimes definitivement.** Corps obligatoire :

```json
{"confirmer": true, "incidents": true}
```

`incidents` vaut `false` par defaut : sans l'option, les resultats et decisions de
session sont conserves. `confirmer` absent, faux, non booleen : **422**, rien efface.
Un incident `en_cours`, y compris en file d'attente, impose **409** sans effacement.
Verification et suppression sont protegees contre une nouvelle soumission concurrente.
Reponse **200** :

```json
{"souvenirs_supprimes": 1, "incidents_supprimes": 2}
```

Apres effacement de session, les identifiants peuvent etre soumis a nouveau.
L'effacement ne remet pas les autres objets injectes (LLM, traceur) a zero et
n'annule pas une action externe deja soumise. Erreur memoire : **500** generique,
la session n'est pas effacee par la route si `vider()` echoue.

## POST /incidents

Corps : `ContexteIncident` valide, avec les champs exacts du contrat.

```json
{
  "incident_id": "inc-demo",
  "horodatage": "2026-10-15T10:42:00Z",
  "signature": "schema_mismatch:pipeline_demo",
  "logs": [{ "template": "ERROR column price missing", "compte": 20 }],
  "commits_recents": [
    {
      "hash": "abc123",
      "message": "rename column price to cost",
      "date": "2026-10-15"
    }
  ],
  "schema": {
    "attendu": ["id:int", "price:float"],
    "observe": ["id:int", "cost:float"]
  },
  "metriques": { "lignes_rejetees": 20.0 }
}
```

Reponse **202** :

```json
{ "incident_id": "inc-demo", "statut": "en_cours" }
```

L'enquete est soumise au thread avant cette reponse. **409** si l'identifiant est
deja enregistre (meme si termine ou en erreur), **422** si le corps est invalide.

## GET /incidents/{id}

Sans corps. Exemple : `GET /incidents/inc-demo`.
Reponse **200** pendant l'execution :

```json
{ "incident_id": "inc-demo", "statut": "en_cours" }
```

Reponse **200** terminee (les chiffres de duree sont illustratifs) :

```json
{
  "incident_id": "inc-demo",
  "statut": "termine",
  "hypotheses": [
    {
      "id": "inc-demo:hyp-1",
      "incident_id": "inc-demo",
      "texte": "Le schema des donnees a change",
      "statut": "retenue",
      "preuves": [
        "schema: colonne manquante price:float",
        "schema: nouvelle colonne cost:float",
        "commits: abc123: rename column price to cost (2026-10-15)",
        "logs: ERROR column price missing (compte=20)"
      ],
      "confiance": 0.95,
      "raison_rejet": null
    },
    {
      "id": "inc-demo:hyp-2",
      "incident_id": "inc-demo",
      "texte": "Saturation des ressources",
      "statut": "refutee",
      "preuves": [],
      "confiance": 0.0,
      "raison_rejet": "Aucune preuve fournie par l'outil"
    },
    {
      "id": "inc-demo:hyp-3",
      "incident_id": "inc-demo",
      "texte": "Probleme reseau ou timeout",
      "statut": "refutee",
      "preuves": [],
      "confiance": 0.0,
      "raison_rejet": "Aucune preuve fournie par l'outil"
    },
    {
      "id": "inc-demo:hyp-4",
      "incident_id": "inc-demo",
      "texte": "Changement de configuration",
      "statut": "refutee",
      "preuves": [],
      "confiance": 0.0,
      "raison_rejet": "Aucune preuve fournie par l'outil"
    }
  ],
  "correctif": {
    "description": "Restaurer le schema attendu de pipeline_demo: id:int, price:float",
    "patch": "{\"composant\":\"pipeline_demo\",\"avant\":[\"id:int\",\"cost:float\"],\"apres\":[\"id:int\",\"price:float\"]}",
    "niveau_risque": "validation",
    "elements_touches": ["pipeline_demo"],
    "reversible": true,
    "justification": "colonne renommee probable: schema: colonne manquante price:float; schema: nouvelle colonne cost:float",
    "confiance": 1.0
  },
  "decision_abstention": {
    "abstient": false,
    "confiance": 1.0,
    "manque": ["incident similaire en mémoire"]
  },
  "metriques": {
    "nb_evenements": 13,
    "nb_hypotheses_testees": 4,
    "nb_ecartees_par_memoire": 0,
    "nb_appels_llm": 1,
    "duree_ms": 20.0,
    "nb_tokens_entree": 0,
    "nb_tokens_sortie": 0
  },
  "decision_humaine": null
}
```

En cas d'abstention, `correctif` vaut `null` et `decision_abstention.abstient`
vaut `true`. Les metriques sont celles de l'enquete : elles n'incluent pas les
evenements de decision humaine ou de resultat Action ajoutes ensuite.
En cas d'exception, le detail original n'est ni expose ni journalise :

```json
{
  "incident_id": "inc-demo",
  "statut": "erreur",
  "message": "Enquete impossible. Consulter la configuration et reessayer."
}
```

**404** si inconnu.

## GET /incidents/{id}/evenements?apres=N

Sans corps. Exemple : `GET /incidents/inc-demo/evenements?apres=0`.
Reponse **200** : liste JSON, y compris pendant l'enquete. Exemple de premiere
lecture partielle :

```json
[
  {
    "incident_id": "inc-demo",
    "etape": 1,
    "type": "decision",
    "acteur": "systeme",
    "texte": "Contexte valide, enquete ouverte",
    "horodatage": "2026-10-15T10:42:00+00:00"
  }
]
```

`apres` vaut `0` par defaut et doit etre positif ou nul. Seules les etapes
strictement superieures sont renvoyees, dans l'ordre. L'interface peut sonder
periodiquement avec la derniere etape recue. Une liste vide `[]` est normale.
**404** si inconnu, **422** si le curseur est invalide.

## POST /correctifs/{id}/decision

`id` est **l'incident_id**, pas un nouvel identifiant de correctif.
Corps :

```json
{ "decision": "appliquer" }
```

Alternative : `{"decision":"refuser"}`. Reponse **202** :

```json
{ "incident_id": "inc-demo", "decision": "appliquer" }
```

La decision est enregistree et ajoute un evenement `decision` / `systeme`.
**Aucune action reelle par defaut.** Le module Action doit brancher l'extension
decrite plus bas. Un refus n'appelle jamais cette extension.
**409** si aucun correctif ou decision deja prise, **404** si incident inconnu,
**422** si decision invalide. Une erreur de l'extension ajoute un evenement
generique sans detail sensible ; elle ne modifie pas le statut de l'enquete.

## POST /incidents/{id}/resultat-action

Corps :

```json
{ "resultat": "echoue" }
```

Alternative : `{"resultat":"reussi"}`. Reponse **202** :

```json
{ "incident_id": "inc-demo", "resultat": "echoue", "memoire_mise_a_jour": true }
```

Met a jour **le dernier enregistrement de meme signature** et ajoute un evenement
`decision` / `systeme`. Ce retour remplace le resultat provisoire de l'enquete.
Avec des incidents simultanes de meme signature, il ne cible pas un enregistrement
par incident : cette limite vient du contrat Memoire actuel.
Si aucun souvenir n'existe, `memoire_mise_a_jour` vaut `false`.
**404** si inconnu, **409** si enquete non terminee, **422** si resultat invalide,
**500** generique si la mise a jour memoire echoue.

## GET /sante

Sans corps. Reponse **200** :

```json
{ "statut": "ok" }
```

Cette route verifie l'API, pas la disponibilite du fournisseur LLM.

## Injection pour Juan, Riq et Action

```python
application = creer_app(
    memoire,
    llm,
    abstention,
    executeur_enquete=lambda tache: tache(),
    executeur_action=lambda incident_id, correctif: traiter(incident_id, correctif),
)
```

`executeur_enquete` recoit une fonction sans argument a soumettre. Omettre ce
parametre utilise le thread de production ; l'execution synchrone est reservee
aux tests. `executeur_action` recoit l'identifiant et un `CorrectifPropose` copie,
uniquement pour `appliquer`. L'appel est soumis au meme executeur.

`enqueter(..., sur_evenement=callback)` appelle le callback immediatement pour
chaque evenement, avec une copie et dans l'ordre. Une exception du callback
interrompt l'enquete et devient un statut d'erreur dans l'API.
