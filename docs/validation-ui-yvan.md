# Validation UI de Yvan

Date : 26 octobre 2026. Perimetre : interface, tests UI et verification des acces.
Aucun changement du backend, des contrats, des fixtures ou du README.

## Menu mobile

Fermeture automatique verifiee sur les sept pages avec un profil Android emule
et une largeur reelle mesuree de 390 x 844 pixels : Incidents, Enquete, Actions,
Memoire, Compteurs, Relations et Exemple P1. Aucune exception Streamlit observee.
Ce controle ne constitue pas un essai sur un telephone physique.

Streamlit 1.66.1 utilise le type d'appareil fourni par le User-Agent pour fermer
le menu apres navigation. Reduire uniquement la largeur d'un navigateur ordinateur
ne declenche pas ce comportement. Le precedent diagnostic de fermeture manuelle
sur telephone etait donc incorrect. La navigation native est conservee, sans
script de clic automatique ni dependance aux classes CSS du menu.

## Accessibilite

La langue choisie est declaree explicitement sur le document et son corps via
un script dans l'UI. Seules les valeurs fixes `fr` et `en` sont autorisees.
AppTest couvre les deux declarations ; Chromium confirme le changement de
`html.lang` et `body.lang`. Aucun texte libre utilisateur n'entre dans ce script.

Un parcours a ete execute avec Orca 50.2, AT-SPI2 2.60.4 et Firefox Nightly 151.0,
dans un affichage Xvfb et un bus D-Bus prives. Les annonces produites par Orca ont
ete consultees dans ses journaux, sans acceder aux autres fenetres du bureau :

- focus et annonce du bouton de lancement ;
- creation d'un incident simule ;
- navigation vers Actions et annonce de Refuser, sans activation ;
- activation du lien Consulter la memoire et arrivee sur la page Memoire.

Les processus Orca, Firefox et Xvfb de ce test ont ete arretes. Xvfb a ete extrait
temporairement dans l'espace utilisateur, sans installation systeme ni sudo.

**Point ouvert :** Firefox/AT-SPI expose encore la langue `en` et Orca choisit une
voix anglaise pour certains libelles francais, malgre la declaration de langue
verifiee dans Chromium. La prononciation francaise sous Firefox n'est donc pas
validee. Une ecoute humaine et une recette NVDA/VoiceOver restent hors de ce controle.
Ce parcours cible ne constitue pas un audit complet de conformite WCAG.

## Interface bilingue

Le selecteur `Langue / Language` propose Francais et English, avec le francais
par defaut. Le choix reste propre a chaque session et est conserve pendant la
navigation et les relancements, y compris apres une injection. Il ne constitue
pas une preference de compte persistante apres la fin de la session.

Les sept pages, boutons, filtres, tableaux, etats, resumes et erreurs UI sont
traduits. Les identifiants, valeurs internes des filtres, preferences de colonnes,
consentements et verrous de decision restent inchanges. La recherche prend en
compte les libelles affiches en anglais sans changer les donnees sous-jacentes.

Les textes recus de l'API (preuves, descriptions, justifications, refutations et
details d'erreur), le SQL et les patchs restent dans leur langue originale.
Aucun service de traduction ni appel LLM supplementaire n'est utilise. Les
libelles natifs de Streamlit, comme `Deploy` et `Expand sidebar`, restent geres
par le framework. La limite Firefox/Orca ci-dessus n'a pas ete revalidee.

AppTest couvre les deux langues, les sept pages, l'isolation des sessions, les
filtres et colonnes conserves, le consentement aux appels reels simule dans le
test, le blocage sur incident incertain et l'absence de renvoi d'une decision.
Chromium confirme la bascule apres injection sur ordinateur (1440 x 1000), puis
les vues anglaises sur mobile Android emule (390 x 844), sans exception ni
debordement horizontal constate. Captures Actions inspectees aux deux tailles.
Le serveur UI a ete redemarre pour eliminer une ancienne version de l'exemple
restee chargee en developpement ; l'API n'a pas ete redemarree.

## Services reels

| Service | Resultat constate | Suite necessaire |
| --- | --- | --- |
| Meterless Nightshift | Jeton Runtime absent ; diagnostic `--sans-chat` non autorise | Renseigner un jeton valide et les voies Nemotron |
| Zetaris | Configuration de connexion et d'organisation absente ; non mesure | Renseigner l'acces et confirmer le contrat REST local |
| Postgres | `DATABASE_URL` non renseignee ; non mesure | Configurer une base de demonstration |
| Langfuse | Serveur joignable, authentification SDK refusee : HTTP 401 | Corriger les cles du projet et verifier sa region |

Aucune enquete reelle ni aucun appel LLM payant n'a ete lance. Aucune trace
d'enquete reelle n'a ete exportee ou declaree validee. Aucun secret n'a ete affiche.

Renseigner les secrets exclusivement dans le fichier local ignore `.env`, jamais
dans une conversation ni dans Git. Le diagnostic Langfuse lit `LANGFUSE_BASE_URL` ;
le traceur d'enquete lit `LANGFUSE_HOST`. Ces adresses doivent designer la meme
region et le meme projet que les cles utilisees.

Apres correction, reprendre les controles de [l'installation de demonstration](INSTALLATION-DEMO.md)
et `scripts/check_langfuse.py --verifier-acces`, puis un parcours UI reel borne,
avec `ACTION_REELLE_DEMO=0`. Verifier explicitement les fournisseurs actifs et la
presence de la trace correspondante dans Langfuse. Un repli fichier/JSON ne vaut
pas une validation Zetaris/Postgres.

## Tests

Suite complete en modes fake/fichier/json : **935 reussis, 3 ignores**.
Les avertissements existants Pydantic et Starlette restent presents.