# Mémoire H-MEM (SQLite)

## Portée

H-MEM est un backend optionnel du protocole `Memoire`, en Python et sans nouvelle
dépendance. `MEMOIRE_PROVIDER=json` reste le défaut ; `postgres` est conservé pour
les installations existantes. Pour activer H-MEM localement :

```bash
MEMOIRE_PROVIDER=hmem
HMEM_PATH=data/hmem.sqlite
python -m rappel.api
```

Le chemin relatif est résolu depuis la racine du dépôt. `data/` est ignoré par Git.
SQLite utilise WAL et des transactions ; une erreur de lecture ou d'écriture est
remontée clairement, sans repli automatique vers JSON. `short_term` reste volatile ;
`working` et `long_term` sont rechargés depuis le fichier. Aucun patch n'est exécuté
par la mémoire.

## Flux d'enquête

1. Pour chaque enquête SQL, le moteur exécute les requêtes selon ses règles habituelles.
2. Une conclusion n'est enregistrée comme fait `factual`/`long_term` que si elle
   existe et si une preuve est une requête SQL actuelle, non vide, exécutée avec
   au moins une ligne et sans erreur. La confiance est celle de la décision
   courante ; un souvenir ne la majore pas.
3. Le fait conserve `source=enquete:<incident_id>`, une provenance `decision`,
   les requêtes SQL (identifiant et texte), la signature, la date, la cause, le
   correctif et les hypothèses réfutées. Les hypothèses restent des enregistrements
   `general` séparés, à confiance plafonnée et avec un tag de statut.
4. Le rappel H-MEM construit sa requête avec la signature, le schéma, les journaux
   réduits et le composant. Le contexte réinjecté est marqué « à revérifier ».
   Il ne constitue jamais une preuve : l'enquête P2 exécute ses propres requêtes SQL,
   et l'abstention P3 ainsi que le rayon d'impact restent les décisions du code.
5. Le résultat `reussi`/`echoue` passe par le feedback H-MEM et inscrit un audit.
   Le résultat non confirmé est marqué dans la provenance ; il n'est pas présenté
   comme un correctif réussi.

`GET /memoire` expose la trace de la dernière recherche H-MEM. `GET /memoire/audit`
est en lecture seule et masque les valeurs sensibles. Le registre SQLite est à
ajout seul, y compris lors de la réinitialisation des souvenirs.

## Services H-MEM

`src/rappel/hmem/` fournit les enregistrements canoniques, le hash FNV-1a
déterministe à 64 dimensions, l'enrichissement, le stockage SQLite, le classement
hybride, le feedback, les conflits, le sommeil et le rêve.

Le classement suit les poids de la référence : 0,35 sémantique, 0,20 Jaccard des
huit premiers mots, 0,10 tags, 0,10 domaine, 0,15 entités, 0,05 couche, 0,05
récence exponentielle de 14 jours, −0,20 souvenir remplacé ; le résultat est borné
à [0, 1] puis multiplié par la confiance. Le seuil par défaut est 0,35 et le top 5.
Un conflit n'est résolu automatiquement que si la confiance de décision atteint
0,70 ; le souvenir perdant reçoit `supersededBy` et reste stocké. Les propositions
de rêve ne deviennent des souvenirs qu'après approbation. Le sommeil fournit un
aperçu, une sauvegarde et une restauration ; ces services ne sont pas branchés au
flux d'enquête.

La référence TypeScript représente `entities` comme une liste de chaînes et
`provenance.refs` comme une liste de chaînes, tandis que la documentation
HOW_TO_USE présente des formes plus larges (entités `{name, type}` et refs objet).
Ce portage suit les formes exécutables de `reference/src/types.ts` pour la
conformité ; les origines de provenance acceptent aussi les valeurs documentées.

## Migration

La migration n'efface jamais la source. Les souvenirs historiques sans preuve SQL
ne sont **pas** promus comme faits confirmés : ils sont importés comme notes
`general`, à faible confiance, étiquetées non vérifiées. Les hypothèses gardent
un enregistrement distinct. L'identifiant dérivé du contenu rend les reprises
idempotentes.

```bash
python scripts/migrer_memoire_vers_hmem.py --simuler
python scripts/migrer_memoire_vers_hmem.py
DATABASE_URL="$DATABASE_URL" python scripts/migrer_memoire_vers_hmem.py
```

Sans `DATABASE_URL`, la source est `MEMOIRE_JSON_PATH` ou `data/memoire.json`.
Avec `DATABASE_URL`, le pilote optionnel PostgreSQL de `requirements-demo.txt` est
nécessaire. Le rapport JSON distingue les éléments déjà présents, importés et
simulés. Conserver une sauvegarde de la source avant la migration.

## PostgreSQL

PostgreSQL sert uniquement à l'ancienne mémoire d'agent, mais reste disponible :
il est encore référencé dans l'installation de démo, les scripts de préparation,
la recette réelle et les tests de compatibilité/intégration. Il ne sera retiré
qu'après validation complète de la migration depuis Postgres et de tous les usages
de remplacement. Le mode mémoire par défaut ne change donc pas dans cette étape.

## Validation et limites

`tests/test_hmem_conformite.py` est un portage des sept sections des vecteurs
officiels dans `tests/fixtures/hmem/`, et non la suite officielle.
`conformance/rappel_hmem.ts` expose le pont synchrone de conformité vers
`scripts/hmem_serveur.py`. Les résultats A (runner TypeScript) et B (portage pytest)
doivent être rapportés séparément ; un portage vert ne constitue pas une preuve
de conformité officielle.

Exécution du runner officiel depuis `engines/hmem/conformance` de Meterless :

```bash
HMEM_IMPL=/chemin/absolu/vers/Rappel/conformance/rappel_hmem.ts npx --yes tsx@4.23.15 runner.ts
```

Avec les fixtures P1/P2/P3 et le LLM fake, la recette a mesuré P1 à 21 événements
et 5 appels LLM, puis P2 à 12 événements et 3 appels ; P2 a retrouvé trois pistes
réfutées via H-MEM, sans les retester, et a exécuté ses propres requêtes SQL.
Ces chiffres sont simulés côté LLM et source SQL (SQLite fixture), pas une mesure
Nightshift/Zetaris.

Les embeddings sont déterministes et sans modèle externe. L'audit local ne remplace
pas une sauvegarde hors machine. Avant une démo réelle, vérifier séparément
Nightshift et Zetaris ; les fixtures et le backend SQL simulé ne sont pas une preuve
de service réel.
