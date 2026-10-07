# Module Enquete + Memoire

## 1. Role en cinq lignes

1. Recevoir un contexte d'incident valide, sans assurer son ingestion.
2. Faire proposer des hypotheses par le LLM et rechercher leurs indices avec des outils deterministes.
3. Utiliser la memoire de meme signature pour eviter des pistes deja refutees et rappeler des correctifs passes.
4. Rendre un correctif a valider ou une abstention, avec evenements et metriques.
5. Exposer ces resultats au CLI et a l'API, sans executer de correction reelle par defaut.

## 2. Entrees, sorties et graphe

Sources : [graphe.py](../src/rappel/enquete/graphe.py), [contrats.py](../src/rappel/contrats.py), [api.py](../src/rappel/api.py).
Les contrats partages n'incluent pas les categories internes des hypotheses.

```python
enqueter(
    contexte, memoire, llm, abstention,
    resultat="reussi", sur_evenement=None,
    war_room_active=None, war_room_max_tours=None,
    war_room_max_appels_llm=None,
    traceur=None,
)
```

`contexte` est un `ContexteIncident` ou un dictionnaire valide. `ResultatEnquete`
contient `hypotheses`, `evenements`, `correctif`, `decision_abstention`,
`enregistrement`, `metriques`. Correctif et enregistrement peuvent valoir `None`.
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

### Tracage optionnel (A14)

[traceur.py](../src/rappel/interfaces/traceur.py) definit le Protocol `Traceur` :
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

`creer_app(..., traceur=None)` construit `creer_traceur()` par defaut, y compris
pour l'instance API globale. Sans les deux cles, sans paquet, ou si la liaison
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
`nb_objections_sans_reponse`, `nb_appels_llm_war_room`.
Les hypotheses testees sont les passages effectifs dans les outils ; les candidats
ecartes sont comptes lors du filtrage memoire. Les appels/tokens sont des differences
d'usage fournisseur ; sans compteur d'appels disponible, le graphe utilise le sien.
Les tokens inconnus valent `None`, jamais une estimation inventee.
La duree utilise `perf_counter`, construction du graphe comprise. Les metriques
d'evenements n'incluent pas les decisions humaines ajoutees ensuite par l'API.

## 3. Lancement

Depuis la racine du depot, dans Git Bash, avec le venv Python 3.11.9 :

```bash
./.venv/Scripts/python.exe -m pytest
LLM_PROVIDER=faux LLM_FALLBACK= ./.venv/Scripts/python.exe scripts/demo_enquete.py
LLM_PROVIDER=faux LLM_FALLBACK= ./.venv/Scripts/python.exe scripts/demo_enquete.py --war-room --json data/demo.json
./.venv/Scripts/python.exe -m uvicorn src.rappel.api:app --host 127.0.0.1 --port 8000
```

La demo ne lance pas de serveur. P1/P2 partagent une memoire temporaire vide et
P3 en utilise une autre vide. Code `0` si P1/P2 concluent et P3 s'abstient sans
correctif, sinon `1`. Sans `--war-room`, le debat est explicitement desactive,
meme si l'environnement l'active.
API interactive : http://127.0.0.1:8000/docs ; aussi `/redoc` et `/openapi.json`.
Voir [api.md](api.md) pour les routes, les corps et les reponses.
L'identifiant des routes de correctif est aussi l'identifiant d'incident.
`GET /incidents` liste les resumes recents (limite 50, maximum 200, filtre statut)
sans logs ni textes d'evenements, avec dates de reception et decisions Action.
`GET /memoire` expose les souvenirs et refutations dedoublonnees, avec filtre exact
de signature : ces textes sont potentiellement sensibles, usage local seulement.
`POST /memoire/reinitialiser` efface les souvenirs avec `confirmer=true` strict ;
`incidents=true` efface aussi la session. Route destructive sans authentification,
refusee si une enquete est en cours, meme encore en file.

Essais manuels utilisant les fournisseurs configures dans `.env` :

```bash
./.venv/Scripts/python.exe scripts/essai_llm.py --confirmer
./.venv/Scripts/python.exe scripts/essai_detective.py --fixture fixtures/contexte_p1.json --repetitions 3 --confirmer
./.venv/Scripts/python.exe scripts/essai_war_room.py --fixture fixtures/contexte_p1.json --tours 2 --max-appels 6 --confirmer
```

`essai_llm.py`, les essais detective/war room et la demo exigent `--confirmer`
pour un fournisseur reel, meme en secours. Sans cette option, ils refusent le
lancement et affichent les appels maximaux prevus ainsi que le rappel de cout.
Avec le fournisseur faux et sans secours reel, aucune confirmation n'est requise.
Les deux essais acceptent `--brut` : masquage des secrets de l'environnement et
400 caracteres maximum par reponse brute. Leurs fonctions sont testees avec des
fournisseurs factices, mais leurs commandes ne sont pas lancees automatiquement
par les tests. La fonction du script de connexion est egalement testee avec un
fournisseur simule, sans lancer sa commande ni effectuer d'appel reseau.
Un plafond d'appels n'est pas un budget en euros : verifier tarifs et quotas.

## 4. Toutes les variables d'environnement

Sources : [llm.py](../src/rappel/interfaces/llm.py), [war_room.py](../src/rappel/enquete/war_room.py),
scripts et [.env.example](../.env.example). `creer_llm` et les adaptateurs HTTP
chargent `.env` du repertoire courant avec `override=False` : l'environnement
deja present a priorite. War room lit `os.environ` et ne charge pas `.env` seule.

| Nom                       | Role                                                 | Defaut reel                                   |
| ------------------------- | ---------------------------------------------------- | --------------------------------------------- |
| `LLM_PROVIDER`            | `faux`, `nvidia` ou `claude`                         | `faux`, aussi si vide                         |
| `LLM_FALLBACK`            | Secours sur `LLMIndisponible`                        | Aucun, chaine vide                            |
| `NVIDIA_API_KEY`          | Authentification NVIDIA                              | Aucun ; obligatoire pour NVIDIA               |
| `NVIDIA_BASE_URL`         | Racine HTTP, suffixee par `/chat/completions`        | `https://integrate.api.nvidia.com/v1`         |
| `NVIDIA_MODEL`            | Modele NVIDIA                                        | Aucun ; obligatoire                           |
| `ANTHROPIC_API_KEY`       | Authentification Anthropic                           | Aucun ; obligatoire pour Claude               |
| `ANTHROPIC_MODEL`         | Modele Claude                                        | Aucun ; obligatoire                           |
| `LLM_TEMPERATURE`         | Temperature HTTP                                     | `0.0`, conversion numerique sans borne metier |
| `LLM_MAX_TOKENS`          | Maximum de generation par requete HTTP               | `1024`, entier >= 1                           |
| `LLM_TIMEOUT_S`           | Timeout HTTPX, pas duree maximale d'enquete          | `60.0`, strictement positif                   |
| `LLM_MAX_APPELS`          | Appels physiques par execution, faux/secours compris | `20`, entier >= 1                             |
| `WAR_ROOM_ACTIVE`         | Interrupteur debat                                   | `0`, accepte seulement `0`/`1`                |
| `WAR_ROOM_MAX_TOURS`      | Tours maximum                                        | `2`, entier >= 0                              |
| `WAR_ROOM_MAX_APPELS_LLM` | Budget physique du debat, relances/secours compris   | `6`, entier >= 0                              |
| `LANGFUSE_PUBLIC_KEY` | Cle publique pour la factory du traceur optionnel | Vide ; sans les deux cles, traceur nul |
| `LANGFUSE_SECRET_KEY` | Cle secrete pour la factory du traceur optionnel | Vide ; jamais affichee |
| `LANGFUSE_HOST` | Hote a transmettre au futur adaptateur SDK | Vide ; aucun hote distant choisi par ce code |
| `LANGFUSE_ENVOYER_TEXTES` | Envoyer les textes des evenements ; `0` les retire | `1` ; une valeur vide conserve l'envoi |

Les parametres war room d'`enqueter` surchargent ces trois variables. Elles sont
presentes dans `.env.example`, toutes avec une valeur vide ; les defauts sont
appliques par le code, pas inscrits dans ce fichier.
Le chemin memoire et le seuil d'abstention sont des parametres de constructeurs,
pas des variables lues par ce module. Host/port passent par les options Uvicorn.
L'URL Anthropic et `anthropic-version=2023-06-01` sont constantes de l'adaptateur.

## 5. Brancher une nouvelle brique

### Memoire, par exemple un futur adaptateur Meterless

Le Protocol de [memoire.py](../src/rappel/interfaces/memoire.py) a les six
methodes ci-dessous. Signature et textes refutes se comparent exactement.
La mise a jour cible le dernier souvenir de la signature et retourne `False`
s'il n'existe pas. Meterless n'est pas integre : ce squelette ne suppose aucune
API externe et n'est pas une implementation utilisable telle quelle.

```python
from typing import Literal
from src.rappel.contrats import EnregistrementMemoire

class MemoireExterne:
    def __init__(self, stockage):
        self.stockage = stockage

    def chercher(self, signature: str) -> list[EnregistrementMemoire]:
        raise NotImplementedError("Lecture et validation Pydantic a implementer")

    def lister(self) -> list[EnregistrementMemoire]:
      raise NotImplementedError("Tous les souvenirs dans l'ordre d'ecriture")

    def vider(self) -> int:
      raise NotImplementedError("Effacer tous les souvenirs et retourner leur nombre")

    def ecrire(self, enregistrement: EnregistrementMemoire) -> None:
        raise NotImplementedError("Persistance a implementer")

    def hypotheses_refutees(self, signature: str) -> list[str]:
        return list(dict.fromkeys(
            texte for souvenir in self.chercher(signature)
            for texte in souvenir.hypotheses_refutees
        ))

    def mettre_a_jour_resultat(
        self, signature: str, resultat: Literal["reussi", "echoue"]
    ) -> bool:
        raise NotImplementedError("Mettre a jour le dernier souvenir correspondant")
```

`MemoireJSON(chemin="data/memoire.json")` cree dossier/fichier automatiquement,
valide a la lecture, ajoute des souvenirs et fusionne les refutations sans doublon
en gardant l'ordre. `src/rappel/memoire/__init__.py` est vide : l'implementation
actuelle est dans `interfaces/memoire.py`.
`lister()` valide et renvoie tous les souvenirs dans l'ordre d'ecriture ; `vider()`
valide le fichier puis ecrit une liste vide et renvoie le nombre supprime.
L'API appelle ces methodes obligatoires sans detection optionnelle de leur presence.

### Fournisseur LLM

Protocol exact de [llm.py](../src/rappel/interfaces/llm.py) : attribut
`usage: UsageLLM`, methode `completer(prompt: str, role: str = "detective") -> str`.
Les roles sont encodes dans le contenu HTTP, pas utilises directement comme roles
OpenAI. Le fournisseur retourne du texte et compte les requetes physiques ; les
tokens inconnus restent `None`.

```python
from src.rappel.interfaces.llm import UsageLLM

class FournisseurExterne:
    def __init__(self, client, budget):
        self.client = client
        self.budget = budget
        self.usage = UsageLLM()

    def debut_execution(self) -> None:
        self.budget.commencer()

    def completer(self, prompt: str, role: str = "detective") -> str:
        raise NotImplementedError("Transport, budget, usage et erreurs assainies a implementer")

    def fermer(self) -> None:
        self.client.close()
```

`debut_execution` et `fermer` ne sont pas dans le Protocol, mais le graphe/scripts
les appellent lorsqu'ils existent. Le budget de ce squelette est une abstraction
a implementer, pas une nouvelle API publique du depot. Pour limiter des sous-appels
physiques pendant un debat, le code actuel utilise l'attribut interne `_budget`
(`maximum`, `appels`). Sans cet attribut, le compteur du debat verifie la limite
entre appels mais ne peut pas limiter les sous-appels internes d'un nouvel adaptateur.

Respecter les formats de candidats et de debat. Lever `LLMIndisponible` sans
detail sensible en cas de panne et `LLMBudgetEpuise` pour le budget. Le secours
ne bascule que sur `LLMIndisponible`, pas sur configuration invalide/budget epuise.
Pour un nouveau nom configurable, ajouter son routage dans `_creer_fournisseur` ;
l'agent ne change pas.

### Abstention

Protocol de [abstention.py](../src/rappel/interfaces/abstention.py) :
`decider(hypothese: Hypothese, nb_sources: int, incident_similaire: bool)` retourne
`DecisionAbstention(abstient: bool, confiance: float, manque: list[str])`.
Ce squelette delegue a la politique actuelle, sans inventer de nouvelle regle :

```python
from src.rappel.contrats import Hypothese
from src.rappel.interfaces.abstention import AbstentionSeuil, DecisionAbstention

class AbstentionPersonnalisee:
    def __init__(self, seuil: float = 0.6):
        self.politique = AbstentionSeuil(seuil)

    def decider(
        self, hypothese: Hypothese, nb_sources: int, incident_similaire: bool
    ) -> DecisionAbstention:
        return self.politique.decider(hypothese, nb_sources, incident_similaire)
```

Injecter les instances dans `enqueter(contexte, memoire, llm, abstention)` ou
`creer_app(memoire, llm, abstention, ...)`. Attention : importer `api.py` construit
aussi son instance globale avec les dependances d'environnement.
Son extension `executeur_action(incident_id, correctif)` n'est appelee que pour
la decision humaine `appliquer`. Le graphe n'execute pas Action.

## 6. Regles de decision

### Candidats et memoire

Le prompt detective contient signature, logs/comptes, ecarts de schema et commits.
Il demande des candidats generiques, pas un verdict. La validation impose au moins
trois objets, `texte` non vide et `categorie` parmi schema/ressources/reseau/configuration,
sans champ supplementaire. L'extraction tolere bloc de code ou texte autour du JSON.
Une relance est autorisee ; deux reponses invalides donnent une liste vide et une
abstention propre. Une panne fournisseur sans secours hors debat peut lever une
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
debat comptent les relances et le secours avec les adaptateurs actuels.

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

- Tests hors reseau avec LLMFaux, LLM scriptes et HTTPX MockTransport. Les adaptateurs
  reels existent, mais la suite ne certifie ni connexion avec de vraies cles ni
  qualite d'un vrai modele. Les essais manuels restent necessaires.
- Seuils choisis/verifies sur trois fixtures et perturbations synthetiques ; la
  confiance est un score heuristique, pas une probabilite calibree.
- Les mots-cles peuvent manquer une panne ou produire un indice non causal.
  Les metriques du contexte ne sont pas utilisees par les outils actuels.
- Memoire JSON locale : reecriture complete, pas de verrou interne, pas d'ecriture
  atomique ni concurrence multi-processus. LLM/budget/usage sont mutables : ne pas
  partager les memes instances entre enquetes concurrentes directes.
- API sans authentification, sans persistance des incidents, sans eviction ni
  borne explicite de file. Un worker thread serialise les enquetes ; un seul
  processus Uvicorn sur 127.0.0.1 est recommande, pas d'exposition publique.
- Aucun calcul de risque ni action reelle par defaut. Un retour Action hors ordre
  peut modifier le mauvais souvenir si plusieurs incidents partagent une signature.
- NVIDIA : verifier URL, chat completions, parametres acceptes par le modele,
  `choices[0].message.content`, `prompt_tokens`/`completion_tokens`.
- Anthropic : verifier `/v1/messages`, version 2023-06-01, corps/messages,
  `content[0].text`, `input_tokens`/`output_tokens` et limites du modele. Le code
  ne rassemble pas plusieurs blocs texte et ne gere ni streaming ni tool calls.
- Erreurs fournisseur assainies, cle absente du `repr` de l'adaptateur ; toutefois
  les contextes/reponses peuvent etre sensibles. Ne pas mettre de secrets dans
  les logs. Aucun filtre general n'assainit les evenements reussis du graphe/API.
- Tracage optionnel teste par injection seulement : liaison au SDK Langfuse reel
  non branchee. Les textes sont envoyes par defaut au client injecte sans filtre
  de secrets ; choisir `LANGFUSE_ENVOYER_TEXTES=0` pour ne pas les transmettre.
- Pas d'integration Meterless ni d'ingestion dans ce module.
  L'avertissement Pydantic sur le champ partage `schema` est connu ; le nom est conserve.

## 8. Checklist A1 a A15

Le statut concerne les tests presents, pas une certification en production.

| Case | Etat                                   | Preuve dans les tests                                                                                                                                                            |
| ---- | -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A1   | Fait                                   | Hypotheses/statuts : [test_enquete.py](../tests/test_enquete.py), `test_a1_hypotheses`                                                                                           |
| A2   | Fait                                   | Retenue avec preuves non vides : `test_a2_preuves_retenues`                                                                                                                      |
| A3   | Fait                                   | Evenements valides, etapes croissantes, callback : `test_a3_evenements` et voisin                                                                                                |
| A4   | Fait                                   | Correctif valide, justification de renommage : `test_a4_correctif`                                                                                                               |
| A5   | Fait                                   | Souvenir avec refutations : `test_a5_memoire`                                                                                                                                    |
| A6   | Fait                                   | Refutees non retestees, outils espionnes : [test_memoire_influence.py](../tests/test_memoire_influence.py), `test_a6_refutees_non_testees`                                       |
| A7   | Fait, critere revise                   | Hypotheses testees du second incident <= 60 % du premier ; ratio d'evenements seulement informatif                                                                               |
| A8   | Fait                                   | Memoire contraire change la decision : `test_a8_memoire_contraire`                                                                                                               |
| A9   | Fait                                   | Incident ambigu : abstention/manque/aucun correctif : [test_abstention_calibration.py](../tests/test_abstention_calibration.py), `test_a9_incident_ambigu`                       |
| A10  | Fait                                   | Trois decisions attendues, bruit/indice faible/egalite au seuil : `test_a10_decisions_dans_un_meme_parcours` et voisins                                                          |
| A11  | Fait                                   | Trois acteurs, evenements et plafonds locaux/globaux : [test_war_room.py](../tests/test_war_room.py)                                                                             |
| A12  | Fait                                   | Debat desactive identique au graphe sans noeud, temps fixes pour comparaison : `test_a12_identique_au_graphe_sans_noeud`                                                         |
| A13  | Fait en simulation ; reel a faire      | Factory, formats HTTP, usage, erreurs et secours : [test_llm_fournisseurs.py](../tests/test_llm_fournisseurs.py)                                                                 |
| A14 | Code pret, activation avec les cles, liaison au SDK a verifier | Cycle debut/evenements/fin, absence de fuite dans les erreurs et non-regression testes avec client injecte dans [test_traceur.py](../tests/test_traceur.py). Adaptateur SDK non branche : aucune trace distante garantie. |
| A15  | A faire ; non mesure                   | P1 rejouee 5 fois avec un vrai LLM doit donner la meme conclusion au moins 4 fois sur 5. Necessite une cle et une mesure reelle ; les tests factices ne valident pas ce critere. |

[test_api.py](../tests/test_api.py), [test_scripts_essai.py](../tests/test_scripts_essai.py)
et [test_demo_enquete.py](../tests/test_demo_enquete.py) ajoutent des validations
d'integration locale, de confirmation de cout et d'export. Les tests du traceur
valident A14 par injection, pas l'envoi effectif au service Langfuse. La stabilite
avec un vrai LLM d'A15 reste non mesuree.
