# Module Enquete + Memoire

## 1. Role en cinq lignes

1. Recevoir un contexte d'incident valide, sans assurer son ingestion.
2. Faire proposer des hypotheses par le LLM et les mettre a l'epreuve par des requetes SQL
   executees par le code (`mode_preuves="sql"`, defaut) ou par des outils deterministes (`"outils"`).
3. Utiliser la memoire de meme signature pour eviter des pistes deja refutees et rappeler des correctifs passes.
4. Rendre un correctif a valider ou une abstention, avec evenements et metriques.
5. Exposer ces resultats aux scripts de demo, sans executer de correction reelle.

Integration dans le squelette de l'equipe (branche `naomy/integration`) : le moteur vit dans
`src/rappel/enquete/` et s'appuie sur les interfaces du squelette (`rappel.interfaces.llm`,
`rappel.interfaces.memoire`, `rappel.interfaces.tools_zetaris`, `rappel.config`). L'API
(`rappel.api`, voir [api.md](api.md)) garde le contrat Phase 0/1 du squelette et lance le
moteur dans un thread a chaque `POST /incidents`. Origine du code : [ORIGINE-DU-CODE.md](ORIGINE-DU-CODE.md).

| Fichier | Role |
| --- | --- |
| [modeles.py](../src/rappel/enquete/modeles.py) | Modeles Pydantic du moteur (ContexteIncident, Hypothese, EvenementEnquete...) ; les JSON Schema de `contrats/` restent la reference, validee par `rappel.contrats.valider` |
| [protocoles.py](../src/rappel/enquete/protocoles.py) | Protocoles LLM, Memoire, Abstention, RunSql, Traceur ; `UsageLLM`, `DecisionAbstention`, voies |
| [adaptateurs.py](../src/rappel/enquete/adaptateurs.py) | `LLMEnquete` / `LLMFaux` / `creer_llm_enquete`, `MemoireEnquete`, plafond `LLM_MAX_APPELS` |
| [graphe.py](../src/rappel/enquete/graphe.py), [boucle_sql.py](../src/rappel/enquete/boucle_sql.py) | `enqueter`, modes `outils` et `sql` |
| [run_sql.py](../src/rappel/interfaces/run_sql.py) | Garde-fous SQL, `RunSqlSQLite` (fichier) et `RunSqlZetaris` |

## 2. Entrees, sorties et graphe

Sources : [graphe.py](../src/rappel/enquete/graphe.py), [modeles.py](../src/rappel/enquete/modeles.py).
Les contrats partages n'incluent pas les categories internes des hypotheses.

```python
enqueter(
    contexte, memoire, llm, abstention,
    resultat="reussi", sur_evenement=None,
    war_room_active=None, war_room_max_tours=None,
    war_room_max_appels_llm=None,
    traceur=None,
    mode_preuves="sql",          # "sql" (defaut, MVP v4) ou "outils" (ancien chemin deterministe)
    run_sql=None,                # RunSql injecte ; sinon creer_run_sql(contexte) selon SOURCE_PROVIDER
    max_tours=None,              # plafond d'appels LLM du mode sql ; defaut MAX_TOURS_ENQUETE (6)
    fabrique_run_sql=None,       # fabrique (contexte -> RunSql)
)
```

`contexte` est un `ContexteIncident` ou un dictionnaire valide. `ResultatEnquete`
contient `hypotheses`, `evenements`, `correctif`, `decision_abstention`,
`enregistrement`, `metriques`, `mode_preuves`, `source_preuves` (mode du backend
SQL : `simule-sqlite`, `zetaris` ; `None` en mode outils) et `requetes_sql` (requetes
proposees avec SQL execute, resultat ou erreur). Correctif et enregistrement peuvent valoir `None`.
Le graphe ci-dessous est celui du mode `outils` ; le mode `sql` est decrit en section 2 bis.
Il n'y a pas d'interface source/ingestion dans ce module.

```text
START
  -> charger_contexte
  -> consulter_memoire
  -> generer_hypotheses
  -> tester_hypotheses
  -> war_room
  -> evaluer_confiance
       | abstient = True  -> abstention -> END
       | abstient = False -> conclure -> ecrire_memoire -> END
```

`creer_graphe` construit un LangGraph `StateGraph` a etat `TypedDict` et capture
les dependances injectees. Le resultat n'est pas stocke dans un etat global.

| Noeud                | Lit                                                       | Produit                                          | Evenements                                                                                                                                                      |
| -------------------- | --------------------------------------------------------- | ------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `charger_contexte`   | Contexte fourni                                           | Validation/revalidation Pydantic                 | `decision`, `systeme` : ouverture                                                                                                                               |
| `consulter_memoire`  | Signature, `chercher`, `hypotheses_refutees`              | Souvenirs, textes refutes                        | `preuve`, `historien` : aucun incident similaire ou correctifs/resultats/dates et refutations                                                                   |
| `generer_hypotheses` | Resume du contexte, detective, refutations                | Hypotheses ouvertes, categories internes         | `hypothese`, `detective` par candidat accepte ; `rejet`, `historien` par texte deja refute ; `abstention`, `systeme` si JSON toujours invalide                  |
| `tester_hypotheses`  | Chaque hypothese et outil de sa categorie                 | Preuves, retenue/refutations, confiance initiale | `preuve`, `systeme` pour la retenue ; `rejet`, `systeme` pour les autres ou la liste vide                                                                       |
| `war_room`           | Retenue, alternatives, souvenirs, limites                 | Confiance ajustee et compteurs                   | `debat` historien/sceptique/detective ; `preuve`, `systeme` pour verification ; `decision`, `systeme` pour relance/bilan. Silence si desactivee ou sans retenue |
| `evaluer_confiance`  | Retenue, familles de sources, souvenirs                   | `DecisionAbstention`                             | `decision`, `systeme` : confiance et abstention                                                                                                                 |
| `abstention`         | Decision et manque                                        | Fin sans correctif ni ecriture memoire           | `abstention`, `systeme` : manque                                                                                                                                |
| `conclure`           | Retenue, categorie, ecart de schema, signature, souvenirs | `CorrectifPropose`                               | `decision`, `systeme` : proposition a valider                                                                                                                   |
| `ecrire_memoire`     | Correctif, hypotheses refutees, resultat provisoire       | `EnregistrementMemoire`, appel `ecrire`          | `decision`, `historien` : enregistrement provisoire                                                                                                             |

Une etape vaut `len(evenements) + 1`, l'horodatage est genere en UTC ISO.
`sur_evenement` recoit une copie profonde immediatement apres chaque emission,
dans l'ordre. Une exception du callback interrompt l'enquete.
`ecrire_evenements_jsonl(evenements, chemin)` cree les parents et ecrit une ligne
JSON par evenement ; la fonction n'impose ni dossier ni extension.

### 2 bis. Mode de preuves SQL (defaut, MVP v4)

Sources : [boucle_sql.py](../src/rappel/enquete/boucle_sql.py),
[run_sql.py](../src/rappel/interfaces/run_sql.py) (contrat backend : [run_sql.md](run_sql.md)).
Principe : **le LLM propose, le code verifie**. Un tour = un appel LLM, plafond
`MAX_TOURS = 6` (`max_tours`). La war room (hors MVP) n'existe qu'en mode `outils`.

```text
START -> charger_contexte -> consulter_memoire -> proposer_hypotheses -> juger
      -> evaluer_confiance
           | abstient -> abstention -> END
           | sinon    -> conclure -> (correctif ? ecrire_memoire : abstention) -> END
```

| Etape | Voie / role | Ce que fait le code |
| --- | --- | --- |
| `proposer_hypotheses` | rapide, `hypotheses` | Prompt : signature, resume du contexte, description de run_sql, `TABLES`, 4 requetes types, hypotheses deja refutees a ne pas reproposer. Reponse : au moins 2 objets `{texte, sql, categorie?}`. Les textes deja refutes sont ecartes (`rejet`), chaque SQL est valide puis execute (`preuve`, identifiant `q1`, `q2`...). |
| `juger` | rapide, `verdict` | Le modele recoit les resultats (10 lignes max par requete) et rend `{verdicts: [{id, statut, requetes_citees, raison}], requetes_suivi}` ; au plus `MAX_REQUETES_SUIVI = 2` requetes de suivi, chaque relance de verdict coute un tour. |
| validation par le code | aucun appel | Retenue seulement si le verdict cite une requete **de cette hypothese**, executee, sans erreur et a resultat non vide ; sinon `refutee` avec `raison_rejet = "aucune preuve SQL"`. Une seule retenue : la plus etayee (egalite : premiere). |
| `evaluer_confiance` | aucun appel | `abstention.decider(retenue, nb_sources, incident_similaire)` avec `nb_sources` = tables distinctes des requetes citees a resultat non vide et `incident_similaire` = la memoire a repondu. |
| `conclure` | profonde, `conclusion` puis `correctif` | Cause confirmee puis `CorrectifPropose` valide par Pydantic, `niveau_risque="validation"` impose, confiance <= celle du code, justification completee par les identifiants de requetes s'ils manquent. Si la memoire contient un correctif `reussi` pour la signature, un seul appel `correctif` renvoie aussi `cause_confirmee` (economie d'un appel profond). |

Confiance initiale (constante nommee `CONFIANCE_PAR_REQUETE_NON_VIDE`) :

```text
c_initiale = min(CONFIANCE_MAX = 0.95, 0.45 x nombre de requetes citees a resultat non vide)
c_finale   = formule inchangee de AbstentionSeuil (seuil 0,6, +0,1 si nb_sources >= 2, +0,1 si incident similaire)
```

Appels d'outils natifs (`LLM_APPEL_OUTILS=1`, defaut `0`) : pour les roles `hypotheses` et
`verdict`, la boucle passe `tools=[RUN_SQL_TOOL]` (de `tools_zetaris`) et accepte, en plus du
JSON texte, des `tool_calls` dont `function.arguments` est un JSON avec `"sql"`. Le SQL d'un appel
complete, dans l'ordre, les hypotheses du tableau sans champ `sql` (ou les `requetes_suivi` sans
`sql` du verdict) ; s'il n'y a aucun texte, chaque appel devient une hypothese libellee
« Hypothese du modele nN (appel run_sql) ». Toute requete passe par `valider_sql`. Un outil
inconnu, des arguments illisibles, une requete vide ou un appel sans hypothese designee donnent
un evenement `rejet` explicite (« Appel d'outil run_sql rejete : ... »), jamais une exception ;
plusieurs instructions sont refusees par `valider_sql`. Le mode par defaut est inchange.

Robustesse aux reponses d'un vrai modele ([test_robustesse_modele.py](../tests/test_robustesse_modele.py)) :
texte autour du JSON et bloc ```json acceptes ; JSON tronque, texte sans JSON, liste vide ou
hypotheses sans `sql` : une relance (qui coute un tour) puis abstention propre ; SQL avec `;` final
ou dans un bloc ```sql nettoye avant validation ; table hors liste blanche refusee sans execution ;
verdict citant une requete inexistante : hypothese refutee ; statut de verdict inconnu : relance
puis abstention. Portabilite SQL : voir [run_sql.md](run_sql.md#8-portabilite-sql-dialecte-zetaris-inconnu).

Plafond : `max_tours` vaut `Configuration.max_tours_enquete` (MAX_TOURS_ENQUETE) par defaut. S'il est atteint avant la conclusion, evenement `decision`
« Plafond de N tours atteint : appel ... non effectue, abstention sans correctif »
(N = valeur reelle), puis abstention dont `manque` contient
« plafond de N tours atteint avant la conclusion ». Reponse JSON invalide : une
relance (qui coute un tour), puis abstention propre.

Evenements : un `EvenementEnquete` par etape. `modele` = `derniere_reponse_info["modele_repondu"]`
(valeur renvoyee par le service) pour les evenements issus d'un appel LLM
(hypotheses, preuves, verdicts, cause, correctif), `None` sinon. Les `preuve`
portent `requete_sql` = SQL reellement execute (apres normalisation, `LIMIT 50` compris)
et leur texte indique le mode du backend (`[simule-sqlite]`) : une preuve simulee n'est
jamais presentee comme reelle. Les SQL refuses donnent un `rejet` sans `requete_sql`
(rien n'est execute) ; un SQL execute en erreur donne un `rejet` avec `requete_sql`.
Acteurs : `detective` pour hypotheses et preuves, `systeme` pour le reste.

#### Calibration de l'abstention (mode sql, LLM faux, fixtures de l'equipe)

Seuil unique `SEUIL_ABSTENTION` (0,6) lu par `rappel.interfaces.abstention.decider` et
par `AbstentionSeuil`. Mesure AVANT modification, constante 0,3 heritee du mode outils :

| Scenario | Hypothese retenue | Requetes non vides citees | Confiance initiale | Tables (nb_sources) | Incident similaire | Confiance finale | Decision |
|---|---|---|---|---|---|---|---|
| P1 | Le schema des donnees a change | 2 | 0.60 | 3 | False | 0.70 | correctif |
| P2 | Le schema des donnees a change | 1 | 0.30 | 2 | True | 0.50 | **abstention (a tort)** |
| P3 | Probleme reseau ou timeout | 1 | 0.30 | 1 | False | 0.30 | abstention |
| P1 + bruit (3 logs) | Le schema des donnees a change | 2 | 0.60 | 3 | False | 0.70 | correctif |
| P3 + log sans rapport | Probleme reseau ou timeout | 1 | 0.30 | 1 | False | 0.30 | abstention |

APRES (`CONFIANCE_PAR_REQUETE_NON_VIDE = 0.45`) :

| Scenario | Hypothese retenue | Requetes non vides citees | Confiance initiale | Tables (nb_sources) | Incident similaire | Confiance finale | Decision |
|---|---|---|---|---|---|---|---|
| P1 | Le schema des donnees a change | 2 | 0.90 | 3 | False | 1.00 | correctif |
| P2 | Le schema des donnees a change | 1 | 0.45 | 2 | True | 0.65 | correctif |
| P3 | Probleme reseau ou timeout | 1 | 0.45 | 1 | False | 0.45 | abstention |
| P1 + bruit (3 logs) | Le schema des donnees a change | 2 | 0.90 | 3 | False | 1.00 | correctif |
| P3 + log sans rapport | Probleme reseau ou timeout | 1 | 0.45 | 1 | False | 0.45 | abstention |

P3 (timeout) contient un vrai log `TimeoutError` : l'indice est retenu par le code mais
reste isole (une table, pas de memoire), d'ou l'abstention. Une requete non vide seule,
sans memoire, reste sous 0,6. Tests : [test_calibration_sql.py](../tests/test_calibration_sql.py),
accord des deux politiques a la limite : [test_adaptateurs.py](../tests/test_adaptateurs.py).

#### Metriques P1 / P2 / P3 (demo, LLM faux, memoire partagee P1->P2)

| Scenario | Evenements | Hypotheses testees | Ecartees memoire | Appels LLM | Tours | Requetes SQL | Rapide/profonde | Decision |
|---|---|---|---|---|---|---|---|---|
| P1 | 21 | 4 | 0 | 5 | 5 | 5 | 3/2 | correctif propose |
| P2 | 12 | 1 | 3 | 3 | 3 | 1 | 2/1 | correctif propose |
| P3 | 19 | 4 | 0 | 3 | 3 | 5 | 3/0 | abstention |

P2/P1 : evenements 57 %, appels LLM 60 % (critere <= 60 %).

#### Fil d'evenements P1 (mode sql, extrait de [fixtures/moteur/evenements_p1.json](../fixtures/moteur/evenements_p1.json))

Les sorties du moteur sont generees dans `fixtures/moteur/` par
`scripts/generer_fixtures_partagees.py` : les fixtures de l'equipe (`fixtures/*.json`)
ne sont jamais reecrites.

```text
01 decision  systeme   -            Contexte valide, enquete ouverte (preuves SQL : simule-sqlite)
02 decision  systeme   -            aucun incident similaire
03 hypothese detective faux-rapide  Le schema des donnees a change
04 preuve    detective faux-rapide  q1 [simule-sqlite] ... 2 ligne(s) ; apercu [["absente","amount",...],["nouvelle","total",...]]
05-10        hypotheses ressources / reseau / configuration et leurs preuves q2-q4 : 0 ligne
11 decision  systeme   faux-rapide  Verdict provisoire (tour 2) : 1 requete(s) de suivi demandee(s)
12 preuve    detective faux-rapide  q5 [simule-sqlite] ... 1 ligne(s) ; commit "rename amount->total"
13 decision  systeme   faux-rapide  Verdict du modele (tour 3) : hyp-1 retenue ['q1', 'q5'] ; hyp-2..4 refutee
14 decision  systeme   -            Hypothese retenue par le code : ... (preuves SQL : q1, q5 ; confiance initiale 0.90)
15-17 rejet  systeme   -            ressources / reseau / configuration : refutee (requetes sans resultat)
18 decision  systeme   -            Confiance 1.00; abstention=False; tables a preuve non vide : [colonnes_attendues, colonnes_observees, commits]
19 decision  systeme   faux-profond Cause confirmee : colonne renommée (amount -> total)
20 decision  systeme   faux-profond Correctif propose pour validation: Restaurer le schema attendu de orders_etl: order_id:integer, amount:decimal
21 decision  systeme   -            Resultat provisoire enregistre en memoire
```

### Tracage optionnel (Langfuse)

[traceur.py](../src/rappel/enquete/traceur.py) implemente le protocole `Traceur` (defini dans `protocoles.py`) :
`demarrer(incident_id, signature)`, `evenement(EvenementEnquete)` et
`terminer(metriques: dict, decision: str)`. `enqueter(..., traceur=None)` utilise
`TraceurNul`, sans charger le SDK. Le demarrage intervient au premier evenement
du contexte valide, puis un appel par evenement, puis la fin avec les metriques
et `correctif_propose` ou `abstention`. Une enquete en erreur apres demarrage
termine la trace avec seulement `duree_ms` et `erreur`, jamais le detail d'exception.
Si la validation echoue avant le premier evenement, aucune trace n'est demarree.

Le traceur est branche sur la callback existante sans modifier les decisions ni
les evenements. Une callback utilisateur conserve sa semantique : ses erreurs
interrompent l'enquete ; les erreurs du traceur, elles, sont ignorees, comptees
dans `nb_erreurs` du traceur protege et averties une seule fois par instance.
Les compteurs ne sont pas ajoutes aux metriques de decision. La duree mesuree
peut inclure le temps passe a transmettre les evenements.

`creer_traceur()` construit le traceur a partir des variables `LANGFUSE_*`. Sans les deux cles, sans paquet, ou si la liaison
SDK est indisponible, retour `TraceurNul` et un avertissement generique au plus
une fois par processus. Aucun paquet n'a ete ajoute au projet.

`TraceurLangfuse(client)` accepte un client d'injection minimal. Son contrat local
est `client.demarrer(dict)`, `client.evenement(dict)`, `client.terminer(dict)` :
ces noms ne sont PAS presentes comme des methodes du vrai SDK. Le debut contient
uniquement `incident_id` et `signature`, les evenements `type`, `acteur`, `etape`
et, par defaut, `texte`. La fin contient uniquement `metriques` et `decision`.
Ni contexte brut, prompts, en-tetes, cles ni objets de correctif ne sont transmis
separement. Les textes peuvent toutefois contenir des extraits de logs/commits.
`LANGFUSE_ENVOYER_TEXTES=0` retire completement le texte des evenements transmis.

**Code pret, activation avec les cles, liaison au SDK a verifier.** Actuellement,
`_AdaptateurLangfuseSDK` est volontairement non implemente : meme si le paquet
est installe et les cles presentes, la factory revient au traceur nul tant que
cette classe n'est pas branchee. Aucune trace distante n'est garantie a ce stade.
Apres installation manuelle, verifier la version et son code/documentation pour :
construction/authentification du client et option host, creation d'une trace racine,
association des evenements a cette trace, mise a jour/finalisation avec metriques
et decision, export/flush/fermeture et erreurs synchrones ou asynchrones.
Verifier aussi les donnees effectivement envoyees et desactiver tout enrichissement
automatique qui transmettrait d'autres donnees. Concentrer ces appels dans cette
seule classe, sans deviner les noms du SDK.

### Metriques

`MetriquesEnquete` contient `nb_evenements`, `nb_hypotheses_testees`,
`nb_ecartees_par_memoire`, `nb_appels_llm`, `duree_ms`, `nb_tokens_entree`,
`nb_tokens_sortie`, `nb_tours_war_room`, `nb_objections`,
`nb_objections_sans_reponse`, `nb_appels_llm_war_room`, et pour le mode sql
`nb_tours` (appels LLM du graphe, relances comprises), `nb_requetes_sql`
(requetes transmises a run_sql), `nb_appels_rapide`, `nb_appels_profonde`
(comptes par le graphe selon `voie_pour_role`). Ces quatre champs valent 0 en mode outils.
Les hypotheses testees sont les passages effectifs dans les outils ; les candidats
ecartes sont comptes lors du filtrage memoire. Les appels/tokens sont des differences
d'usage fournisseur ; sans compteur d'appels disponible, le graphe utilise le sien.
Les tokens inconnus valent `None`, jamais une estimation inventee.
La duree utilise `perf_counter`, construction du graphe comprise. Les metriques
d'evenements n'incluent pas les decisions humaines ajoutees ensuite par l'API.

## 3. Lancement

Depuis la racine du depot (`pytest.ini` ajoute `src` et la racine au chemin) :

`pip install -r requirements.txt` installe aussi `langgraph`, requis par le moteur et ses
tests ; `requirements-demo.txt` n'ajoute plus que `psycopg`.

```bash
pytest --ignore=tests/test_ui.py
uvicorn rappel.api:app --app-dir src               # API : contrat du squelette + enquete en thread
python scripts/demo_enquete.py                     # P1, P2, P3 en mode sql, LLM_PROVIDER=fake
python scripts/demo_enquete.py --mode outils --war-room --json data/demo.json
python scripts/stabilite_p1.py                     # mode sql par defaut, --mode outils possible
python scripts/generer_fixtures_partagees.py --verifier
python scripts/verifier_pret.py                    # configuration, securite du depot, demo a blanc
python scripts/recette_module_a.py                 # recette A1-A15 ; data/recette.json ; --confirmer si service reel
python scripts/check_postgres.py                   # memoire Postgres : connexion, tables, aller-retour annule
python scripts/check_meterless.py                  # script de l'equipe : appel reel Nightshift
```

La demo ne lance pas de serveur. P1/P2 partagent une memoire JSON temporaire et P3 en
utilise une autre. Code `0` si P1/P2 concluent et P3 s'abstient sans correctif, sinon `1`.
Le tableau affiche `Tours`, `Requetes SQL`, `Appels rapide/profonde` et `Source preuves`
(`simule-sqlite (SIMULE)` en mode fichier, `zetaris` sinon) : une preuve simulee n'est
jamais presentee comme reelle. `--war-room` n'a d'effet qu'avec `--mode outils`.
Avec `LLM_PROVIDER=meterless`, demo et stabilite exigent `--confirmer` (appels reels).

Scripts non importes de l'ancien depot : `essai_llm.py`, `essai_detective.py` et
`essai_war_room.py` ciblaient NVIDIA et Claude en direct, ce que l'equipe exclut (tout passe
par Meterless). Le diagnostic reel se fait avec `check_meterless.py` et `check_zetaris.py`
du squelette ; `check_meterless.py` de l'ancien depot n'a pas ete repris.

## 4. Configuration unique

Toute la configuration passe par `rappel.config.Configuration` (fichier `.env` a la racine,
modele `.env.example`, aucun secret suivi) :

| Variable | Champ | Usage par le moteur |
| --- | --- | --- |
| `LLM_PROVIDER` | `llm_provider` | `fake` -> `LLMFaux` (hors reseau) ; `meterless` -> `LLMEnquete(MeterlessLLM)` |
| `METERLESS_BASE_URL`, `METERLESS_RUNTIME_TOKEN` | idem | API Nightshift compatible OpenAI ; jeton jamais affiche |
| `METERLESS_MODEL`, `METERLESS_MODEL_RAPIDE`, `METERLESS_MODEL_PROFOND` | idem | Lane rapide : `detective`, `outil`, `hypotheses`, `verdict` ; lane profonde : `sceptique`, `conclusion`, `correctif` ; repli `METERLESS_MODEL` |
| `SOURCE_PROVIDER` | `source_provider` | Backend des preuves : `fichier` -> `RunSqlSQLite` (mode `simule-sqlite`), `zetaris` -> `RunSqlZetaris` |
| `ZETARIS_BASE_URL`, `ZETARIS_API_KEY`, `ZETARIS_ORG_ID` | idem | Utilises par `tools_zetaris.run_sql` |
| `MEMOIRE_PROVIDER`, `MEMOIRE_JSON_PATH`, `DATABASE_URL` | idem | Backend de `MemoireEnquete` : `json` ou `postgres` |
| `SEUIL_ABSTENTION` | `seuil_abstention` | Seuil unique de `decider` et `AbstentionSeuil` (0,6) |
| `MAX_TOURS_ENQUETE` | `max_tours_enquete` | Plafond de tours de la boucle sql (6) |
| `LLM_MAX_APPELS` | `llm_max_appels` | Plafond d'appels LLM par execution, relances comprises (20) |
| `LLM_APPEL_OUTILS` | `appel_outils` | `0` (defaut) : requetes dans le JSON du texte ; `1` : appels d'outils natifs `run_sql` acceptes en plus |

Variables hors configuration unique, conservees pour des briques optionnelles :
`WAR_ROOM_*` (war room, hors MVP, desactivee par defaut) et `LANGFUSE_*` (traceur optionnel).

## 5. Brancher une nouvelle brique

### Memoire

Le protocole `Memoire` ([protocoles.py](../src/rappel/enquete/protocoles.py)) manipule des
objets Pydantic : `chercher`, `lister`, `vider`, `ecrire`, `hypotheses_refutees`,
`mettre_a_jour_resultat`, `ecrire_hypotheses`. `MemoireEnquete(backend=None)` l'implemente
au-dessus du backend du squelette choisi par `MEMOIRE_PROVIDER` (`MemoireEnquete.json(chemin)`
pour un fichier explicite). Les backends `MemoireJSON` et `MemoirePostgres`
([memoire.py](../src/rappel/interfaces/memoire.py)) manipulent des dictionnaires valides par
les JSON Schema et exposent les memes methodes, plus `lister_hypotheses(signature=None)`.

- `MemoireJSON` : ecriture atomique, fichier des incidents cree a la premiere ecriture ;
  table `hypotheses` dans `<nom>_hypotheses.json` (meme identifiant = ligne remplacee).
- `MemoirePostgres` : `psycopg` importe paresseusement, toutes les requetes parametrees
  (`%s`), aucune valeur concatenee ; `hypotheses` en `INSERT ... ON CONFLICT (id) DO UPDATE`.
- `MemoireAvecSecours(principal, secours)` : avec `MEMOIRE_PROVIDER=postgres`, le backend est
  Postgres avec secours JSON (`MEMOIRE_JSON_PATH`). Si Postgres leve « Connexion a la memoire
  Postgres impossible » (ou si le pilote manque), l'appel bascule sur le JSON, une seule alerte
  assainie est journalisee et `mode` vaut `fallback-json` ; chaque appel retente Postgres, donc
  `mode` revient a `postgres` des qu'il repond. Les ecritures faites en secours restent dans le
  JSON (pas de synchronisation automatique). Le mode apparait dans `GET /healthz`
  (`memoire_mode_effectif`) et `GET /incidents/{id}` (`memoire_mode`).
- `scripts/check_postgres.py` : `SELECT 1`, presence des tables de `sql/init.sql`, aller-retour
  ecriture/lecture/suppression dans une transaction annulee ; mot de passe masque, pilote importe
  a la demande. Test d'integration reel : `RAPPEL_TEST_POSTGRES=1 pytest tests/test_postgres_integration.py`.

La boucle appelle `ecrire_hypotheses` apres chaque enquete (deux modes, abstention comprise).
La date d'un enregistrement est au format `AAAA-MM-JJ` (contrat `enregistrement_memoire`).

#### Correspondance avec les tables Postgres ([sql/init.sql](../sql/init.sql))

| Table | Colonne | Source | Note |
| --- | --- | --- | --- |
| `memoire_incidents` | `signature` | `EnregistrementMemoire.signature` | `chercher` = egalite exacte, ordre `id` |
| | `cause_confirmee` | `.cause_confirmee` | justification du correctif |
| | `hypotheses_refutees` | `.hypotheses_refutees` | JSONB ; `hypotheses_refutees(signature)` = union ordonnee sans doublon |
| | `correctif_essaye` | `.correctif_essaye` | |
| | `resultat` | `.resultat` | `mettre_a_jour_resultat` modifie la ligne de plus grand `id` de la signature |
| | `date` | `.date` | `DATE` |
| `hypotheses` | `id` | `Hypothese.id` (`incident:hyp-N`) | cle primaire, upsert |
| | `incident_id`, `signature` | arguments de `ecrire_hypotheses` | |
| | `texte`, `statut`, `confiance`, `raison_rejet` | `Hypothese` | `statut` `ouverte` si plafond avant verdict |
| | `preuves` | `Hypothese.preuves` | JSONB : `"qN [tables] n ligne(s) : SQL execute"` |
| | `cree_le` | horodatage UTC d'ecriture | |

`vider()` vide les deux tables et renvoie le nombre d'enregistrements d'incidents supprimes.

### Fournisseur LLM

Le fournisseur reel reste `rappel.interfaces.llm` : `FakeLLM` et `MeterlessLLM` renvoient
`{texte, tool_calls, modele, usage}`. Aucun appel direct NVIDIA : tout passe par Meterless.
`LLMEnquete(fournisseur)` ([adaptateurs.py](../src/rappel/enquete/adaptateurs.py)) expose le
protocole du moteur : `completer(prompt, role="detective", tools=None) -> str`,
`derniere_reponse_info` (`voie`, `modele_demande`, `modele_repondu`, `tokens_entree`,
`tokens_sortie`), `usage` (appels et tokens, total et `par_voie` rapide/profonde) et le
plafond `LLM_MAX_APPELS`. `modele_repondu` est le champ `model` reellement renvoye par
Nightshift (`MeterlessLLM.dernier_modele_repondu`), jamais le modele demande en repli.
Les `RuntimeError` du fournisseur deviennent `LLMIndisponible` ; un message contenant un
element d'authentification est remplace par un message generique.

`FakeLLM` repond aux roles `hypotheses`, `verdict`, `conclusion`, `correctif` (prompt avec
bloc `DONNEES`) par le JSON deterministe de [_faux_sql.py](../src/rappel/interfaces/_faux_sql.py),
avec `modele` `faux-rapide` ou `faux-profond` ; ses autres reponses sont inchangees.
`LLMFaux` (double de test et de demo) delegue ces roles a `FakeLLM` et produit localement les
reponses JSON historiques du mode outils et de la war room (`detective`, `sceptique`, `historien`).
`creer_llm_enquete()` choisit `LLMFaux` ou `LLMEnquete(MeterlessLLM)` selon `LLM_PROVIDER`.

### Abstention

Le squelette garde `decider(hypothese: dict, contexte: dict)` ; le moteur utilise
`AbstentionSeuil` (meme module) dont le seuil par defaut est `Configuration.seuil_abstention`.
Sans bonus (une source, pas d'incident similaire), les deux politiques donnent la meme
decision a la limite (`confiance == seuil` : pas d'abstention). `DecisionAbstention` et le
protocole `Abstention` sont dans `protocoles.py`.

## 6. Regles de decision

### Candidats et memoire

Le prompt detective contient signature, logs/comptes, ecarts de schema et commits.
Il demande des candidats generiques, pas un verdict. La validation impose au moins
trois objets, `texte` non vide et `categorie` parmi schema/ressources/reseau/configuration,
sans champ supplementaire. L'extraction tolere bloc de code ou texte autour du JSON.
Une relance est autorisee ; deux reponses invalides donnent une liste vide et une
abstention propre. Une panne fournisseur (`LLMIndisponible`) hors debat peut lever une
exception, que l'API transforme en statut `erreur`.

Les textes exactement refutes sont ecartes avant les outils, sans verifier si les
anciennes refutations sont encore vraies. La memoire contraire change donc bien la
decision. Un correctif passe est cite, jamais rejoue automatiquement. Le bonus
`incident_similaire` repose sur l'existence d'un souvenir, meme avec resultat `echoue`.

### Outils, preuves et gagnante

Les outils de [outils.py](../src/rappel/enquete/outils.py) lisent uniquement le
contexte. Chaque template/commit correspondant compte pour une preuve, pas une
preuve par occurrence. Les prefixes `logs:`, `commits:`, `schema:` servent a
compter les familles de sources, pas leur independance causale.

| Outil         | Indices et seuils                                                                                                       |
| ------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Schema        | Colonnes manquantes/nouvelles/types differents ; commits de schema/renommage ; logs colonne/type/KeyError de compte > 0 |
| Ressources    | memory/memoire/disk/disque/oom ; compte > `SEUIL_LOG_RESSOURCES=5`                                                      |
| Reseau        | timeout/connection/connexion/network/reseau ; compte > `SEUIL_LOG_RESEAU=5`                                             |
| Configuration | Commits config/configuration/parametre/parameter, variantes de pluriel                                                  |

Normalisation : minuscules et suppression des accents. Recherche par limites de
mots, variantes/prefixes explicites (`renomm\w*`, `rename(?:d|s)?`). `content-type`
est retire avant la recherche. Ressources/reseau excluent INFO et
passed/healthy/recovered/succeeded/ok, meme si un autre mot parle de panne.
Ce filtre ne concerne pas l'outil schema.

Colonnes : `nom:type`, absent/vide = `TYPE_INCONNU="?"`, noms vides ignores,
premiere occurrence gardee pour les doublons. Aucun ecart de type si un cote est
inconnu. Les types restent des chaines sans catalogue valide. Un nom manquant et
un nouveau nom ne prouvent pas un renommage : la justification dit probable.

Sans preuve : hypothese refutee. Parmi les autres, le maximum de preuves gagne ;
egalite : premier candidat. Les autres hypotheses sont refutees au profit de la
gagnante. La confiance initiale est :

```text
c_initiale = min(0.95, 0.3 * nombre_de_preuves)
```

`0.95` et `0.3` sont des litteraux du graphe, pas des constantes nommees ni des
variables d'environnement.

### War room et abstention

Le debat ne change ni les preuves ni le choix de la gagnante. L'historien resume
la memoire sans LLM ; le sceptique emet une objection et le detective cite des
preuves. Au moins une citation exactement presente dans la retenue suffit : le
code ne verifie pas semantiquement qu'elle repond a l'objection. Le champ
`preuve_manquante` ne declenche pas un nouvel outil.

```text
c_debat = max(0, c_initiale - PENALITE_OBJECTION_SANS_REPONSE * objections_sans_reponse)
PENALITE_OBJECTION_SANS_REPONSE = 0.1
c_finale = min(1.0, c_debat + (0.1 si nb_sources >= 2) + (0.1 si incident_similaire))
abstient = (c_finale < seuil) OU (aucune preuve)
seuil par defaut = 0.6
```

Le seuil vient de `AbstentionSeuil(seuil)`, les bonus sont des litteraux de la
classe. Egalite au seuil avec preuve : pas d'abstention. Une seule source n'est
pas une interdiction absolue si la confiance est assez elevee. `manque` peut etre
non vide meme quand l'agent conclut. Sans retenue : abstention directe.
Pour une retenue schema, le graphe impose aussi un ecart reel, sinon abstention.

Plafond de tours/appels, absence d'objection ou indisponibilite arretent le debat.
JSON invalide : une relance puis arret sans penalite pour cet echange ; les
penalites deja verifiees restent appliquees. Les budgets physique global et du
debat comptent les relances avec l'adaptateur `LLMEnquete`.

### Correctif et souvenir provisoires

`elements_touches` contient la partie apres le premier `:` de la signature, ou
la signature complete sans separateur. Le patch schema est un JSON avant/apres,
pas une migration executable. Les autres categories donnent une proposition
avec categorie/preuves, pas un correctif technique implemente.
`niveau_risque="validation"` et `reversible=True` sont fixes ; la reversibilite
n'est pas prouvee par Action.

Le souvenir ecrit utilise la justification comme `cause_confirmee`, la description
comme `correctif_essaye` et `resultat="reussi"` par defaut. Ces noms ne prouvent
pas une execution ou une confirmation reelle : Action doit renvoyer le resultat
effectif. La mise a jour cible le dernier souvenir de signature identique, pas
un identifiant d'incident.

## 7. Limites connues

- Tests hors reseau : `FakeLLM` / `LLMFaux`, `requests.post` simule pour Meterless,
  `tools_zetaris.run_sql` simule pour Zetaris, faux pilote `psycopg` pour Postgres.
  Meterless Nightshift, Zetaris et Postgres ne sont pas testes en reel ici.
- Zetaris doit exposer les tables de `TABLES` (vues limitees a l'incident) ; le corps REST
  reste a confirmer cote equipe (`CLE_SQL_CORPS` dans `tools_zetaris.py`). Voir [run_sql.md](run_sql.md).
- En mode fake, `GET /incidents/{id}/evenements` reste la vue d'affichage du squelette
  (fixture P1, liste vide sinon) ; le fil du moteur est sur `/incidents/{id}/enquete/evenements`.
- API locale sans authentification, etat des incidents en memoire du processus (un seul worker).
- Seuils choisis sur trois fixtures et perturbations synthetiques ; la confiance est un
  score heuristique, pas une probabilite calibree.
- La fusion conclusion + correctif repose sur un correctif `reussi` de meme signature.
- War room et traceur Langfuse : hors configuration unique, desactives par defaut.
- L'avertissement Pydantic sur le champ `schema` de `ContexteIncident` est connu.

## 8. Checklist A1 a A15 (cahier MVP v4)

Etat mesure par `scripts/recette_module_a.py` en mode fake/fichier/json (23/10/2026). Une case
« reussi (simule) » est verifiee sur les doubles hors reseau ; sa partie reelle reste a mesurer.

| Case | Libelle du cahier MVP v4 | Etat | Partie reelle |
| ---- | ------------------------ | ---- | ------------- |
| A1 | ≥ 2 hypotheses avec statut sur contexte_p1.json | reussi | - |
| A2 | Toute hypothese retenue cite ≥ 1 preuve reelle | reussi | - |
| A3 | Chaque preuve affichee renvoie a une requete SQL executee (requete_sql) | reussi (simule, SQLite) | non mesuree : Zetaris |
| A4 | Chaque etape ecrit un EvenementEnquete valide (evenement_enquete.schema.json) | reussi | - |
| A5 | P1 conclut « colonne renommee » et le CorrectifPropose valide son schema | reussi | - |
| A6 | Arret au plafond (MAX_TOURS_ENQUETE, defaut 6) avec un message clair | reussi | - |
| A7 | Apres P1, EnregistrementMemoire avec hypotheses refutees ecrit en memoire | reussi (simule, JSON) | non mesuree : Postgres |
| A8 | P2 retrouve P1 et ne reteste aucune hypothese refutee | reussi | - |
| A9 | P2 ≤ 60 % des evenements de P1 et moins d'appels LLM | reussi (12/21, 3/5) | - |
| A10 | Memoire contraire : l'agent change de decision | reussi | - |
| A11 | P3 : abstention, aucun correctif, elements manquants listes | reussi | - |
| A12 | Seuil calibre : P1 et P2 sans abstention, P3 avec | reussi | - |
| A13 | Changer de voie ou de modele par configuration (METERLESS_MODEL_RAPIDE/PROFOND) | reussi (deux configurations) | - |
| A14 | En mode reel, le modele renvoye contient « nemotron » | non mesure | non mesuree : Nightshift |
| A15 | P1 rejouee 5 fois : meme conclusion au moins 4 fois | reussi (simule, 5/5) | non mesuree : Nightshift |

Tests : [test_recette_module_a.py](../tests/test_recette_module_a.py), [test_enquete_sql.py](../tests/test_enquete_sql.py),
[test_calibration_sql.py](../tests/test_calibration_sql.py), [test_conformite_contrats.py](../tests/test_conformite_contrats.py),
[test_adaptateurs.py](../tests/test_adaptateurs.py), [test_memoire_secours.py](../tests/test_memoire_secours.py),
[test_api.py](../tests/test_api.py) (equipe) et [test_api_enquete.py](../tests/test_api_enquete.py).
En mode reel : `LLM_PROVIDER=meterless SOURCE_PROVIDER=zetaris MEMOIRE_PROVIDER=postgres
python scripts/recette_module_a.py --confirmer`.
