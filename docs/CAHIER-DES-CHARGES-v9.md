# CAHIER DES CHARGES : « Rappel », copilote d'investigation d'incidents DevOps / MLOps

**Zetaris Demo Challenge** · Version 9, 2 novembre 2026 · Développeur unique · Remplace les versions v4 à v8 (hackathon, équipe de 4, AWS)

> « Un incident, une leçon. Jamais deux fois la même erreur. »

**Statut de ce document** : il décrit la cible **et** l'état réel d'après l'audit du dépôt. Les fonctionnalités non développées ne sont **pas** présentées comme opérationnelles (voir §26 et le rapport d'audit). Le plan d'exécution est dans la roadmap séparée.

---

## 1. Présentation générale de Rappel

Rappel est un copilote destiné aux ingénieurs **DevOps, MLOps, Data Engineers et ML Engineers**. Il aide à **détecter, analyser, diagnostiquer et résoudre** plus vite les incidents qui touchent des pipelines de données et des systèmes de Machine Learning.

Il enquête avec des **preuves vérifiables** (chaque preuve est une requête SQL exécutée dans **Zetaris**), consulte une **mémoire** des incidents et des pistes réfutées, **s'abstient** quand il n'est pas sûr, mesure le **rayon d'impact** d'un correctif, et **laisse l'humain décider**.

L'objectif est un produit **réellement fonctionnel et démontrable**, pas une interface qui simule l'intelligence.

## 2. Contexte et évolution du projet

| Période | Évolution |
|---|---|
| Oct. 2026 | Développement pour l'**Open Agent Hackathon 2026** par une équipe de quatre personnes. Zetaris était déployé sur AWS (cluster, bucket S3) |
| 27 oct. 2026 | Fin de la période de build du hackathon. Le code (97 commits, 934 tests) est en place ; la plupart des mesures sont encore simulées |
| Après le hackathon | **Abandon de la configuration AWS** (problème technique non résolu, volonté de limiter les dépenses). Les anciens membres ne participent plus |
| **29 oct. – 2 nov. 2026** | **Zetaris Demo Challenge** : vidéo de démonstration. Rappel est maintenu par **une seule personne** |

**Historique AWS** (mention seulement) : la lecture SQL de Rappel avait été validée sur un cluster AWS ; les ressources correspondantes (dont un utilisateur IAM de lecture) ne font plus partie de l'architecture et doivent être nettoyées à ton initiative.

## 3. Problématique

En astreinte, la nuit, reconstituer la cause d'une panne demande de croiser des journaux, des changements récents, des schémas, des métriques et l'historique des incidents. On **reteste souvent des pistes déjà écartées**, et une correction faite dans l'urgence peut causer d'autres dégâts.

## 4. Objectifs

**Généraux** : réduire le temps d'investigation, rendre chaque conclusion vérifiable, éviter de refaire les mêmes erreurs, garder l'humain responsable des décisions.

**Spécifiques (Challenge)** :

1. Faire de **Zetaris un composant central, montré en fonctionnement réel**.
2. Produire un **diagnostic fondé sur des requêtes Zetaris**.
3. Utiliser **Nemotron via Meterless Nightshift**, avec preuve du modèle.
4. Montrer une mémoire d'incidents qui change réellement la décision.
5. Livrer une **vidéo conforme** avant la clôture.

## 5. Utilisateurs cibles

Ingénieurs DevOps, MLOps, data engineers et ML engineers, y compris dans des équipes **sans astreinte dédiée**. Ce sont des **hypothèses de marché non validées**.

## 6. Périmètre fonctionnel

| Catégorie | Contenu |
|---|---|
| **Indispensable à la démonstration du Challenge** | Zetaris local ; données synthétiques lues par SQL ; scénario P1 réel ; Nemotron via Nightshift prouvé ; vidéo |
| **MVP complet** | + P2 (mémoire H-MEM), P3 (abstention), rayon d'impact, interface de suivi des incidents |
| **Alert & Approve** | Notification par e-mail, statuts, décisions humaines enregistrées, historique |
| **Hors périmètre** | Données réelles, exécution sur des systèmes de production, ressources AWS, notifications WhatsApp, application mobile native, escalade multi-ingénieurs |

## 7. Description détaillée des fonctionnalités MVP

| Fonctionnalité | Objectif | Fonctionnement attendu | Composants | Données | Critère « terminé » |
|---|---|---|---|---|---|
| **F1 Détection et collecte** | Savoir qu'un pipeline a échoué et réunir les indices | **Python** exécute le pipeline, valide schéma attendu contre observé (échec réel), génère `run_id` unique, logs et événements, puis réduit les logs (voir §10 bis) | `pipeline_demo`, `ingestion` (Python) | `transactions`, `schema_contract`, `observed_schema`, `pipeline_runs`, `incident_logs` | Un incident P1 est généré à chaque exécution, **sans modifier la source** |
| **F2 Enquête fondée sur des preuves** | Identifier la cause probable | Au moins deux hypothèses ; chaque test est une requête Zetaris ; conclusion avec requêtes citées | Boucle LangGraph, `run_sql`, Nemotron | Résultats SQL | Toute hypothèse retenue cite une requête exécutée ; plafond de 6 tours |
| **F3 Mémoire** | Ne pas refaire les mêmes erreurs | Recherche d'incidents similaires, réutilisation des résolutions **validées**, hypothèses réfutées sautées | `interfaces/memoire`, H-MEM | `incident_history` | P2 retrouve P1 ; aucune proposition marquée validée automatiquement |
| **F4 Proposition de correctif** | Proposer une action prudente | Correctif, risque, confiance, rayon d'impact | `action/rayon_impact` | `CorrectifPropose` | Mapping proposé **seulement si l'équivalence sémantique est confirmée** |
| **F5 Validation humaine et exécution contrôlée** | Garder le contrôle | Décision humaine ; exécution seulement sur le pipeline de démo isolé, avec sauvegarde et retour arrière | `action/executeur` | Journal des décisions | Aucune correction automatique sur des données métier ; un échec restaure l'état d'origine |
| **F6 Abstention** | Ne pas deviner | Preuves insuffisantes ou contradictoires : liste des informations manquantes, aucune correction | `interfaces/abstention` | Seuil de confiance | P3 s'abstient et ne propose aucune correction risquée |
| **F7 Interface** | Rendre l'enquête lisible | Liste des incidents, fil d'enquête, preuves, diagnostic, correctif, décisions | Streamlit | API | Parcours P1 complet sans ligne de commande |

## 8. Scénarios P1, P2 et P3

| Scénario | Comportement attendu |
|---|---|
| **P1 : incompatibilité de schéma** | Le pipeline attend `amount`, le schéma observé contient `total`. Python lit les données et métadonnées dans Zetaris ; la validation échoue **réellement** ; un événement d'incident et un `run_id` sont créés ; logs et preuves conservés ; Nemotron analyse ; Rappel propose un mapping `total` vers `amount` **seulement si l'équivalence est confirmée** |
| **P2 : incident récurrent** | Même incident, nouveau `run_id`. Rappel retrouve une résolution **validée** dans H-MEM, compare anciennes et nouvelles preuves, décide si elle s'applique encore et **explique ce qui est réutilisé** |
| **P3 : incident ambigu** | Preuves insuffisantes ou contradictoires : distinguer faits et hypothèses, lister ce qui manque, demander une vérification, **refuser toute correction risquée** |

Les trois scénarios doivent être **reproductibles et testables**. État actuel : code et tests simulés présents ; **non mesurés en réel** (voir §26).

## 9. Fonctionnalité Alert & Approve

**Objectif** : informer l'ingénieur d'un incident et lui laisser décider. **État : non implémentée** (aucun code d'e-mail ni de statuts ; seule existe une décision appliquer/refuser sans identité).

**Workflow cible** : détection, notification par e-mail, consultation de la fiche, lecture du diagnostic et des preuves, décision, enregistrement, exécution contrôlée **si le mécanisme existe**, vérification.

**Statuts** : Detected, Investigating, Awaiting Approval, Approved, Rejected, More Information Required, Resolved, Failed. **Une approbation n'est pas une exécution réussie.** Si l'exécution n'existe pas, Rappel affiche une instruction de correction **sans prétendre l'avoir appliquée**.

**Critères « terminé »** : e-mail réellement reçu avec identifiant, scénario, gravité, description, horodatage et lien ; décisions approuver, refuser et « investigation approfondie » testées ; historique des décisions persistant.

## 10. Architecture technique actualisée

```text
┌────────────────────── MacBook Pro (macOS, Intel i7, 16 Go) ─────────────────────┐
│  Zetaris local (Docker) ◄── SQL ── Backend Rappel (Python, FastAPI, LangGraph)  │
│   tables : transactions, schema_contract, ...        │        │                 │
│                                                      │        └─ Interface (Streamlit)
│   Mémoire H-MEM (SQLite local) ◄────────────────────┘                          │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │ Wi-Fi local : POST /v1/chat/completions
┌─────────────── PC Windows (Intel i7 8e gén., 16 Go) ────────────────────────────┐
│  Meterless Nightshift Runtime ──► fournisseur NVIDIA ──► Nemotron              │
└─────────────────────────────────────────────────────────────────────────────────┘
```

Aucune ressource cloud AWS. Les secrets restent dans des variables d'environnement.

## 10 bis. Qui fait quoi : Python, Zetaris, Nightshift/Nemotron et l'humain

**Principe : Python orchestre.** C'est le backend Python de Rappel (sur le Mac) qui exécute le pipeline de démo, les validations, les incidents contrôlés et la collecte des logs. Ni Zetaris ni le modèle ne le font.

| Acteur | Ce qu'il fait | Ce qu'il ne fait pas |
|---|---|---|
| **Python (backend et pipeline de démo)** | Exécute le pipeline sur une **copie isolée** des données (`data/pipeline_demo`) ; **valide** schéma attendu contre schéma observé ; **injecte les incidents contrôlés** (`injecteur p1`, `p2`, `p3`, `reset`) sur cette copie ; **génère les logs et l'état** réellement observés (`pipeline.log`, `etat.json`) ; crée les événements d'incident ; **collecte les preuves** en envoyant des requêtes SQL à Zetaris ; **orchestre les appels** au LLM ; applique les garde-fous (liste blanche SQL, plafonds, rayon d'impact, abstention) ; exécute un correctif **uniquement sur le pipeline de démo isolé, après décision humaine** ; écrit la mémoire | Ne modifie **jamais** les CSV sources (`pipeline_demo/donnees/`) ; n'écrit pas dans Zetaris ; n'applique rien sur des données métier |
| **Zetaris** | Couche SQL en **lecture seule** sur les sources déclarées (`transactions`, `schema_contract`, et les tables d'observabilité si elles sont déclarées) ; fournit les preuves interrogeables | N'exécute **pas** le pipeline ; ne génère **pas** les logs ; ne provoque pas les incidents |
| **Nightshift et Nemotron** | Raisonnent : hypothèses, conclusion, proposition de correctif, à partir des preuves que Python leur transmet | Ne lisent pas les données directement ; n'exécutent rien ; **leurs réponses sont des propositions**, le code décide |
| **L'humain** | Approuve, refuse ou demande plus d'informations | |

**Déroulement d'une exécution (P1)** :

1. Python **injecte** l'incident dans la copie isolée (renommage de `amount` en `total`).
2. Python **exécute le pipeline** : il écrit ses logs et son état.
3. Python **valide** : la comparaison schéma attendu (lu dans `schema_contract` via Zetaris) contre schéma observé **échoue réellement**.
4. Python **crée l'événement d'incident**, avec un identifiant d'exécution unique.
5. Python **collecte les preuves** : requêtes SQL envoyées à Zetaris, résultats conservés avec la requête.
6. Python **envoie** les preuves à Nemotron via Nightshift et reçoit un diagnostic.
7. Python **applique les garde-fous** et calcule le rayon d'impact ; mapping `total` vers `amount` proposé seulement si l'équivalence est confirmée.
8. L'humain **décide** ; Python exécute **sur le pipeline isolé** si autorisé, **vérifie** le retour au vert et restaure en cas d'échec.
9. Python **enregistre** l'incident et la résolution dans la mémoire.

**Deux écarts à connaître (d'après l'audit du dépôt)** :

- **Les logs et le schéma observé sont produits localement** par Python (copie CSV, base SQLite). Zetaris ne les voit que s'ils sont déclarés dedans (inconnue I5). Pour que Zetaris soit réellement central, les preuves principales doivent être **lues dans Zetaris** : au minimum `transactions` et `schema_contract`, idéalement aussi une table de journaux.
- **Le code ne génère pas d'identifiant `run_id`** (seul un `incident_id` existe côté API). **À ajouter** pour respecter le modèle `pipeline_runs`.

---

## 11. Architecture Zetaris locale

| Élément | Contenu |
|---|---|
| **Machine** | MacBook Pro 2019, Intel Core i7, 16 Go, macOS. Les images Zetaris sont amd64 : **exécution native sur Intel**. Le test officiel a été réalisé sur Apple Silicon : **Intel non testé** |
| **Prérequis** (dépôt officiel `zetaris/getting-started`) | Docker 25 ou plus, **8 Go alloués à Docker**, 4 CPU, 15 Go de disque ; téléchargement d'un paquet depuis un compte Zetaris Cloud et identifiants de registre ; **licence d'évaluation de 30 jours** sans commande signée |
| **Installation** | `./preflight.sh`, `docker compose pull`, `docker compose up -d`, `./verify.sh` (9 contrôles) ; environ 20 à 30 min, 4 Go à télécharger |
| **Ports** | 3000 (interface), 8888 (REST `/api/v1.0`), 10000 (JDBC) |
| **Connexion Python** | Passerelle UI (`/api/proxy/sql-editor/sqls/run-query`) ou REST directe ; en-têtes `Authorization: Bearer`, `X-Org-ID`, `X-Request-ID` neuf à chaque appel ; une seule instruction SQL par appel ; clé API créée **par toi** dans l'interface |
| **Sources de données** | Un fichier CSV local **n'est pas lisible par un chemin documenté**. Option A : Postgres Docker déclaré par `CREATE DATASOURCE` (JDBC) puis `REGISTER DATASOURCE TABLE`. Option B : stockage S3 local (MinIO) et `CREATE LIGHTNING FILESTORE TABLE`. Les deux **à vérifier** |
| **Remplacement par Postgres interdit** | Postgres n'est qu'une **source** de données ; **Zetaris reste la couche SQL** par laquelle Python lit |
| **Persistance** | À vérifier par un redémarrage des conteneurs |

## 12. Architecture Nightshift / Nemotron sur PC Windows

Nightshift tourne **sur le PC Windows** et ne s'exécute plus sur la machine d'un ancien membre. Tout appel au modèle passe par lui (API compatible OpenAI : `GET /v1/models`, `POST /v1/chat/completions`) ; la clé NVIDIA reste **uniquement dans Nightshift**. Rappel utilise deux voies : **rapide** (hypothèses, outils) et **profonde** (conclusion, correctif).

**Ne pas utiliser `meterless/auto` comme preuve.** Chaque événement d'enquête enregistre le **modèle renvoyé par le service** (`modele`) ; Nemotron n'est « utilisé » que si ce champ le montre.

À vérifier (pas de documentation publique trouvée) : identifiant exact du modèle, mode d'exécution (distant via NVIDIA ou local), GPU, coûts d'appel.

## 13. Communication réseau entre les deux machines

`127.0.0.1` désigne **la machine locale** : il ne fonctionne pas du Mac vers le PC. Validation en 10 étapes : adresse IPv4 privée du PC ; mode d'exposition du Runtime ; Hotspot ou équivalent ; interface d'écoute ; pare-feu Windows (réseau privé seulement) ; accessibilité du port depuis le Mac ; authentification ; `GET /v1/models` ; `POST /v1/chat/completions` ; modèle Nemotron renvoyé. Le jeton Runtime est dans `.env`, jamais dans les journaux.

## 14. Modèle de données

Modèle logique **souhaité** (à vérifier contre les possibilités réelles de Zetaris) :

| Table | Colonnes | Où | État |
|---|---|---|---|
| **transactions** | transaction_id, buyer_id, amount, recipient_id, transaction_date, status | Zetaris (source : CSV via Postgres ou stockage S3) | Données prêtes (500 lignes) ; chargement local à faire |
| **schema_contract** | dataset_name, column_name, expected_type, required | Zetaris | Données prêtes (6 lignes) ; colonne `required` à vérifier |
| **observed_schema** | scenario_id, dataset_name, column_name, observed_type | Observabilité | À définir |
| **pipeline_runs** | run_id, scenario_id, started_at, status, rows_processed, error_code | Observabilité | À définir |
| **incident_logs** | event_id, run_id, timestamp, severity, component, message | Observabilité | À définir |
| **incident_history** | incident_id, error_signature, root_cause, resolution, validated | Mémoire | Équivalent existant : enregistrements H-MEM |

**Règle** : Python **n'écrit jamais dans les CSV sources**. Les données d'observabilité sont écrites dans une base complémentaire (Postgres local ou JSONL) puis, si possible, déclarées dans Zetaris pour être relues comme preuves. Toutes les données sont **synthétiques**.

## 15. Gestion de la mémoire H-MEM

La mémoire est derrière l'interface `memoire.chercher / memoire.ecrire`. **H-MEM est exécuté localement** (portage Python avec stockage SQLite, trois niveaux court terme, travail, long terme, provenance, remplacement, audit en lecture seule). Une conclusion n'est promue en fait confirmé que si une requête SQL actuelle l'appuie. Un souvenir réinjecté est « à revérifier » et **ne constitue jamais une preuve**. Postgres et JSON sont des secours. **Ne pas présenter H-MEM comme un service Meterless distant.**

## 16. Gestion des logs et des incidents

Chaque exécution produit un `run_id`, des événements horodatés, des preuves avec leur requête SQL et le **modèle** utilisé. Les logs sont réduits avant analyse. Le journal ne contient jamais de secret. **Persistance des incidents après redémarrage : à vérifier.**

## 17. Notifications par e-mail

Voir §9. Contenu : identifiant, scénario, gravité, description, horodatage, lien vers la page de détail. Fournisseur : SMTP configurable ; **aucun service payant** ; tests avec un SMTP local. **Non terminée tant qu'elle n'a pas été testée de bout en bout.**

## 18. Interface utilisateur

Streamlit, 7 pages (Incidents, Enquête en direct, Actions, Mémoire, Compteurs, Relations, Exemple P1). Elle affiche preuves, requêtes SQL, modèle utilisé, diagnostic, correctif, risque, confiance, décisions. Les actions appellent réellement l'API. Un badge « simulé » signale tout résultat non réel.

## 19. Sécurité et contrôle des accès

Aucun secret dans Git ; `.env` ignoré ; `.env.example` fictif ; jetons absents des journaux ; clé Zetaris créée par toi ; clé NVIDIA uniquement dans Nightshift ; garde-fou SQL (lecture seule, une instruction, liste blanche, 50 lignes) ; l'API locale **n'a pas d'authentification** : ne pas l'exposer hors du Mac. Aucune opération destructive ; **ne pas détruire de ressource sans ton autorisation**.

## 20. Gestion des erreurs et des incertitudes

Service coupé : message explicite, **pas de repli silencieux** vers des données locales (mode Zetaris strict). Plafonds de tours et d'appels. Abstention en cas de preuves insuffisantes. Tout repli (simulé) est annoncé à l'oral et à l'écran.

## 21. Tests et critères de validation

| Niveau | Contenu | État |
|---|---|---|
| Unitaires et intégration | 934 tests réussis, 4 ignorés, contre de **faux services** | Mesuré (simulé) |
| Recette réelle | `recette_reelle.py --confirmer`, `pret_reel.py`, `collecter_preuves_sponsors.py` | **Non exécutée** |
| Validation du réseau | 10 étapes (§13) | À faire |
| Reproductibilité | `test_demarrage_propre.py`, rejeu 5 fois de P1 | À faire en réel |
| Sécurité | `scan_secrets.py` : propre | Fait |

Les tests contre de faux services **ne sont pas des preuves sponsor**.

## 22. Contraintes matérielles et techniques

16 Go de RAM sur chaque machine ; 8 Go pour Docker sur le Mac ; 15 Go de disque libre ; Python 3.11 ou 3.12 ; même réseau Wi-Fi ; **aucune installation Zetaris testée sur Intel** ; enregistrement d'écran à prévoir sans saturer la mémoire.

## 23. Gestion des coûts

Aucune ressource AWS ; outils gratuits ; Zetaris en évaluation (licence de 30 jours) ; Postgres et MinIO en local ; appels Nemotron plafonnés par le code (`LLM_MAX_APPELS`, `LLM_MAX_APPELS_SESSION`) ; coût réel des appels **inconnu**, à contrôler sur le compte NVIDIA.

## 24. Critères de réussite

Zetaris montré en fonctionnement réel ; P1 réel de bout en bout avec requêtes Zetaris ; Nemotron prouvé ; aucune donnée source modifiée ; aucune correction automatique sur des données métier ; vidéo conforme soumise à temps.

## 25. Exigences du Zetaris Demo Challenge

| Exigence | Détail |
|---|---|
| **Période** | Du 29 octobre 2026 à **23 h 45 UTC** le « Tue 2 Nov » écrit dans le règlement (le 2 novembre 2026 est un **lundi** : vérifier ; viser le lundi) |
| **Éligibilité** | Être inscrit au hackathon ; âge minimum à confirmer ; **consentement de tous les membres** pour une entrée d'équipe |
| **Vidéo** | Zetaris **en fonctionnement réel** ; segment de **40 à 60 secondes** (caméra ou voix off) sur pourquoi Zetaris est impressionnant ; données synthétiques ou publiques ; travail original ; MP4 ou MOV ; anglais ou sous-titres anglais ; durée maximale à confirmer |
| **Réponse écrite** | « In one sentence, why did you think it was amazing? » |
| **Nom du fichier** | `TEAM-NAME_DDMMYY` : `TEAM-MONSTERS_021126` si soumis le 2 novembre |
| **Notation (100)** | Utilisation significative de Zetaris 25 · apport de Zetaris 15 · problème et impact 15 · exécution technique 15 · « Working with SpaceXSI » 15 · créativité 10 · vidéo 5 |
| **Agent de code** | Critère noté, **pas une condition d'entrée**. SpaceXSI n'est pas disponible : GitHub Copilot est montré **en toute transparence** |
| **Départage** | Le score « utilisation de Zetaris » |
| **Licence** | Zetaris peut utiliser la vidéo et citer ta phrase à des fins promotionnelles |

**Points à confirmer dans le règlement officiel** : durée maximale, formats, langue, âge, nombre d'entrées, participation individuelle d'un ancien membre, lien de soumission, date des annonces (voir le rapport d'audit §7).

## 26. Éléments restant à développer (état réel)

| Élément | État |
|---|---|
| Zetaris local sur Mac Intel | **À installer et valider** |
| Chargement des CSV dans Zetaris local | **À réaliser** (option A ou B) |
| Adaptation des preuves « logs/commits » à Zetaris | **À décider** |
| Réseau Mac vers Nightshift sur Windows | **À valider** |
| Preuve Nemotron en réel | **À mesurer** |
| P1, P2, P3 en réel | **À mesurer** (existent en simulé) |
| Notifications e-mail | **Non implémenté** |
| Alert & Approve (statuts, authentification, version, historique persistant) | **Non implémenté** |
| README et documents du dépôt | **À mettre à jour** (le README annonce encore « aucun code ») |
| Vidéo et soumission | **À réaliser** |
| Nettoyage AWS | **À faire par toi** |
