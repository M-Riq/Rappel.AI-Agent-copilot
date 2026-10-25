# Checklist de démo réelle — Zetaris et Meterless Nightshift

Guide de lancement pour une démonstration où les preuves viennent de Zetaris,
les appels LLM passent par Meterless Nightshift et la mémoire utilise Postgres.
Les commandes sont écrites pour PowerShell sous Windows.

> **Ne pas afficher ni copier les secrets dans le dépôt, le terminal partagé, une
> capture d’écran ou la vidéo.** Les identifiants Zetaris sont dans `Zetaris.pdf`.
> Le jeton Runtime Meterless doit être récupéré dans la Console Nightshift. La
> clé NVIDIA reste dans Nightshift : Rappel ne l’utilise jamais directement.

## Conditions de réussite

Avant de présenter l’enquête comme réelle, vérifier que son résultat indique :

- `llm: meterless` et `enquete_simulee: false` ;
- `source: zetaris`, `source_preuves: zetaris` et `preuves_simulees: false` ;
- `memoire_mode: postgres` (pas `fallback-json`) ;
- des événements de preuve qui contiennent les requêtes SQL réellement exécutées ;
- des modèles effectivement renvoyés par Nightshift, y compris la voie rapide et
  la voie profonde.

Le mode par défaut du dépôt est `fake` / `fichier` / `json` : il faut donc
configurer explicitement les trois fournisseurs réels avant de lancer l’API.

## La veille ou avant l’arrivée du public

### 1. Préparer la machine et les dépendances

La configuration de démo prévue par le cahier des charges utilise une seule
machine avec **16 Go de RAM minimum**. Vérifier que Zetaris et Nightshift sont
déjà installés et démarrés ; ils ne sont pas fournis par ce dépôt.

À la racine du dépôt, dans PowerShell :

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Installer les dépendances de démo, dont le pilote Postgres :

```powershell
if (-not (Test-Path .\.venv\Scripts\python.exe)) { py -3.11 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r requirements-demo.txt
```

Dans `.env`, renseigner les valeurs depuis les sources autorisées. Ne jamais
commiter ce fichier.

```dotenv
LLM_PROVIDER=meterless
SOURCE_PROVIDER=zetaris
MEMOIRE_PROVIDER=postgres

METERLESS_BASE_URL=<URL du Runtime dans la Console Nightshift>
METERLESS_RUNTIME_TOKEN=<jeton Runtime Nightshift>
METERLESS_MODEL=<modèle de secours autorisé>
METERLESS_MODEL_RAPIDE=<identifiant Nemotron Lightning du catalogue>
METERLESS_MODEL_PROFOND=<identifiant Nemotron Super du catalogue>

ZETARIS_BASE_URL=<URL REST Zetaris indiquée pour l'installation>
ZETARIS_API_KEY=<clé API créée par Juan et conservée dans Zetaris.pdf>
ZETARIS_ORG_ID=<ID d'organisation indiqué dans Zetaris.pdf>

DATABASE_URL=<garder la valeur locale de .env.example ou adapter à Postgres>
```

Remplacer chaque valeur entre chevrons ; ne pas conserver ces chevrons dans
`.env`. Copier l’URL REST exacte attendue par l’installation Zetaris. Ne pas
utiliser le port interne `8889`. Vérifier dans la documentation REST locale que
le corps de la requête SQL attendu est bien celui configuré dans le projet.

Dans Nightshift, vérifier que les deux voies (« lanes ») ciblent bien les modèles
Nemotron choisis et sont autorisées pour le Runtime. Le script de contrôle
Meterless vérifie un appel dans la voie rapide ; le run réel devra confirmer
aussi la voie profonde.

### 2. Démarrer et tester Postgres

Dans PowerShell :

```powershell
docker compose up -d postgres
.\.venv\Scripts\python.exe scripts/check_postgres.py
```

Le contrôle doit confirmer la connexion, les tables `hypotheses` et
`memoire_incidents`, ainsi qu’un aller-retour de test. Si le contrôle échoue,
corriger le service ou `DATABASE_URL` avant de continuer. Si `sql/init.sql` n’a
pas été appliqué, le service Postgres peut devoir être recréé ou les tables
créées explicitement ; ne pas effacer le volume contenant des données sans
accord.

### 3. Tester les deux fournisseurs réels

```powershell
.\.venv\Scripts\python.exe scripts/check_zetaris.py
.\.venv\Scripts\python.exe scripts/check_meterless.py
```

- Zetaris doit réussir le `SELECT 1` de diagnostic. **Ce test prouve seulement
  que l’appel REST répond** ; il ne prouve pas que les données de l’incident
  sont présentes.
- Meterless doit retourner une réponse et le modèle effectif doit être un
  Nemotron. Le script appelle le rôle `detective`, donc il ne valide que la
  voie rapide si celle-ci est configurée.
- Ne pas poursuivre avec une clé invalide ou en remplaçant un secret par une
  valeur factice.

### 4. Vérifier les vues Zetaris de l’incident

Avant la présentation, Juan vérifie dans Zetaris que les tables ou vues du
pipeline sont lisibles et contiennent les données correspondant à l’incident
choisi. L’agent s’attend aux tables logiques suivantes, limitées au contexte de
l’incident :

| Table | Colonnes attendues |
| --- | --- |
| `logs` | `template`, `compte` |
| `commits` | `hash`, `message`, `date` |
| `colonnes_attendues` | `nom`, `type` |
| `colonnes_observees` | `nom`, `type` |
| `metriques` | `nom`, `valeur` |

Tester de vraies lectures avec ces tables dans Zetaris et confirmer leur
dialecte SQL. `docs/run_sql.md` décrit les formes de requêtes attendues.
**Bloquer la démo si ces tables/vues ou les données de l’incident n’existent
pas : `SELECT 1` seul ne suffit pas.**

### 5. Préparer un contexte d’incident réel

Juan prépare un fichier local `data/incident-live.json` contenant le contexte
observé pour l’incident choisi, conforme à
[`contrats/contexte_incident.schema.json`](../contrats/contexte_incident.schema.json).
Ce fichier doit décrire les mêmes logs, commits, écarts de schéma et métriques
que les données accessibles dans Zetaris.

Ne pas soumettre les fixtures `fixtures/contexte_p1.json`, `p2` ou `p3` en les
présentant comme un incident réel. En dehors du mode fake, une fixture reste un
contexte de test ; elle ne transforme pas les résultats associés en observation
réelle. Ne pas inclure de secrets ni de données personnelles non nécessaires.
Le dossier `data/` est ignoré par Git et peut ne pas encore exister :

```powershell
New-Item -ItemType Directory -Force data | Out-Null
```

## Le jour de la démo — répétition de 10 minutes

### T−10 min — Vérification de configuration

Dans un terminal PowerShell à la racine du dépôt :

```powershell
.\.venv\Scripts\python.exe scripts/verifier_pret.py
```

Ce script vérifie la sécurité du dépôt et lance une démo à blanc en imposant
`fake` / `fichier` / `json` pendant le test. **Sa démo à blanc n’est pas la
démo réelle et ne doit pas être montrée comme telle.** Vérifier que `.env` est
ignoré par Git et qu’aucune clé n’est suivie :

```powershell
git check-ignore .env
git status --short
```

Ne pas continuer si `.env` apparaît comme fichier suivi ou si un secret a été
commité.

### T−8 min — Contrôle rapide des services

Répéter les deux contrôles si la configuration ou les services ont changé :

```powershell
.\.venv\Scripts\python.exe scripts/check_zetaris.py
.\.venv\Scripts\python.exe scripts/check_meterless.py
```

Les credentials ne doivent jamais être passés en argument de ligne de commande
ni affichés dans le terminal partagé.

### T−6 min — Lancer l’API

Dans un premier terminal PowerShell :

```powershell
.\.venv\Scripts\python.exe -m uvicorn rappel.api:app --app-dir src --host 127.0.0.1 --port 8000
```

Dans un second terminal, vérifier les modes annoncés :

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
```

La réponse doit annoncer `llm = meterless`, `source = zetaris` et
`memoire = postgres`. L’API locale n’a pas d’authentification : la garder
écoutant sur `127.0.0.1` et ne pas l’exposer à Internet.

Dans un troisième terminal, lancer l’interface de démonstration réelle :

```powershell
.\.venv\Scripts\python.exe -m streamlit run ui/app.py
```

Ouvrir l’adresse locale affichée par Streamlit. L’interface doit confirmer les
trois modes réels avant de permettre une soumission. Elle n’affiche aucun
événement si l’API est inaccessible et ne charge pas de fixture en secours.

### T−4 min — Soumettre l’incident réel

Charger `data/incident-live.json` dans l’interface puis cliquer sur
**Lancer l’enquête réelle**. L’interface soumet le contexte à l’API et affiche
le fil de l’identifiant reçu seulement lorsque les indicateurs de provenance
confirment Meterless, Zetaris et Postgres.

En cas de répétition ou de diagnostic sans interface, le même contexte peut
être soumis directement à l’API :

```powershell
$contexte = Get-Content .\data\incident-live.json -Raw
$soumission = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/incidents `
  -ContentType "application/json" `
  -Body $contexte
$id = $soumission.id
Write-Host "Incident soumis : $id"
```

L’API démarre l’enquête en arrière-plan et retourne un identifiant. Un contexte
mal formé produit une erreur `422` ; un identifiant déjà soumis dans ce
processus produit `409`. Choisir un nouvel `incident_id` pour la répétition.

### T−3 min — Suivre l’enquête et contrôler la provenance

Attendre la fin de l’enquête (au plus cinq minutes) :

```powershell
$fin = (Get-Date).AddMinutes(5)
do {
  $etat = Invoke-RestMethod "http://127.0.0.1:8000/incidents/$id"
  Write-Host "Statut : $($etat.statut)"
  if ($etat.statut -eq "erreur") { throw "L'enquête a échoué. Vérifier la configuration et les services." }
  if ($etat.statut -ne "termine") { Start-Sleep -Seconds 2 }
} while ($etat.statut -ne "termine" -and (Get-Date) -lt $fin)

if ($etat.statut -ne "termine") { throw "Délai dépassé avant la fin de l'enquête." }
$etat | ConvertTo-Json -Depth 20
```

Vérifier dans le résultat :

```powershell
$etat | Select-Object incident_id, statut, llm, source_preuves, preuves_simulees, memoire_mode, enquete_simulee
```

Valeurs requises : `termine`, `meterless`, `zetaris`, `False`, `postgres`,
`False`. Si une valeur diffère, ne pas annoncer une enquête réelle.

Afficher les événements de cette soumission, notamment les requêtes SQL et les
modèles renvoyés :

```powershell
$evenements = Invoke-RestMethod "http://127.0.0.1:8000/incidents/$id/enquete/evenements"
$evenements | ConvertTo-Json -Depth 20
```

Confirmer qu’au moins une preuve SQL réussit, que ses données correspondent à
l’incident et que les événements d’appels LLM montrent les modèles effectivement
renvoyés par Meterless. Vérifier la voie rapide et la voie profonde avant la
démo ; un catalogue disponible ou un modèle demandé ne prouve pas à lui seul
que les deux voies ont servi pendant l’enquête.

### T−1 min — Présenter le résultat, sans exécuter de patch

Montrer le parcours : contexte réel → hypothèses → requêtes et résultats
Zetaris → modèles Meterless → conclusion ou abstention → correctif proposé et
rayon d’impact → mémoire Postgres.

L’API Phase 0/1 **enregistre une décision mais n’exécute jamais le patch**.
Dire « correctif proposé » et non « correctif appliqué ». Pour cette checklist,
utiliser l’API et les événements de l’incident soumis comme source de vérité.

## Répartition conseillée selon le cahier des charges

- **Juan (B)** : contexte d’incident et ingestion, préparation des données et
  vues Zetaris, clé Zetaris, volet action/rayon d’impact.
- **Naomy (A)** : enquête, mémoire et abstention ; confirmer les hypothèses et
  que les conclusions s’appuient uniquement sur les preuves affichées.
- **Yvan (C)** : lancer l’API et l’interface, surveiller les événements,
  contrats, fixtures et vérifications techniques.
- **Riq** : coordination et décisions ; confirmer les accès sponsors, les
  modèles/lignes autorisés et le déroulé avec l’équipe.

## Limites actuelles à connaître

- `scripts/verifier_pret.py` est un contrôle de préparation et une démo à blanc,
  pas une validation de l’intégration réelle.
- `scripts/check_zetaris.py` vérifie un aller-retour `SELECT 1`, pas les vues du
  pipeline. Tester les cinq tables logiques avec les données du cas.
- `scripts/check_meterless.py` appelle la voie rapide (`detective`). La voie
  profonde doit être confirmée lors d’un vrai run et dans ses événements.
- L’interface `ui/app.py` exige les modes réels, soumet un contexte JSON fourni
  par l’équipe et masque les résultats si les champs de provenance ne confirment
  pas une enquête Meterless/Zetaris/Postgres.
- Les incidents et résultats de l’API sont en mémoire : ils sont perdus au
  redémarrage du serveur. Éviter de redémarrer l’API avant d’avoir terminé la
  présentation.
- Les connexions Zetaris, Meterless et Postgres réelles ne sont pas certifiées
  par la suite de tests du dépôt : la répétition réelle ci-dessus est
  indispensable.

## Arrêt après la démonstration

Arrêter l’API avec `Ctrl+C` dans son terminal. Arrêter Postgres seulement si la
machine n’en a plus besoin :

```powershell
docker compose stop postgres
```

Conserver `.env` hors Git. Ne pas supprimer le volume Postgres dans le cadre de
l’arrêt normal.
