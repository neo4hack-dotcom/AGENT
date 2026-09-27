# AGENT — guide utilisateur

AGENT est un analyste de données qui travaille sur les sources de votre entreprise (bases,
données de marché, référentiels…) sans jamais sortir du réseau privé. Vous posez une question
en français ou en anglais ; il interroge les sources, calcule, trace des graphiques, produit des
fichiers (Excel, CSV, PDF) et dit d'où vient chaque chiffre.

Ce guide s'adresse à ceux qui **utilisent** AGENT. L'installation et l'administration sont
décrites dans le [README](../README.md) et la sécurité dans [SECURITY.md](../SECURITY.md).

- [1. Premiers pas](#1-premiers-pas)
- [2. Bien poser une question](#2-bien-poser-une-question)
- [3. Lire une réponse](#3-lire-une-réponse)
- [4. Graphiques](#4-graphiques)
- [5. Exporter des résultats](#5-exporter-des-résultats)
- [6. Rapports PDF](#6-rapports-pdf)
- [7. Pièces jointes](#7-pièces-jointes)
- [8. Garder la main](#8-garder-la-main)
- [9. Mode Agent ou LLM direct](#9-mode-agent-ou-llm-direct)
- [10. Conversations, raccourcis, mémoire](#10-conversations-raccourcis-mémoire)
- [11. Pour l'administrateur](#11-pour-ladministrateur)
- [12. Dépannage](#12-dépannage)

---

## 1. Premiers pas

L'écran de départ ne contient qu'un champ de saisie. Tapez votre question, puis **Entrée**
(**Maj+Entrée** pour aller à la ligne).

Pendant qu'il travaille, AGENT montre ce qu'il fait : les étapes (*steps*) avec la source
interrogée, la requête exécutée et le nombre de lignes obtenues, et, si le modèle le permet,
son raisonnement repliable (*Thinking*). La réponse s'écrit ensuite sous ces étapes.

En haut à droite, le nom du modèle et un point de couleur indiquent son état : **vert**, il
peut appeler des outils ; **orange**, il répondra de mémoire seulement ; **rouge**, aucun
modèle n'est disponible. Le badge *N tools* compte les outils des sources connectées.

Pour arrêter une réponse en cours, cliquez sur le carré noir (*Stop*) à droite du champ.

## 2. Bien poser une question

AGENT choisit lui-même ses sources, mais une question précise donne une réponse plus rapide et
plus sûre :

| Plutôt que | Préférez |
|---|---|
| « Les trades ? » | « Nombre de trades par mois en 2026, par desk » |
| « Le cours de TTE » | « Cours de clôture de TTE le 30/04/2026, en EUR » |
| « Les risques » | « Utilisation des limites VaR par desk au 30/04/2026, en % de la limite » |

Donnez la **période**, l'**unité** ou la **devise**, et la **définition** quand plusieurs sont
possibles (notionnel ou valeur de marché, date de trade ou de règlement…). Si un point reste
ambigu et change le résultat, AGENT vous pose la question avec quelques choix cliquables
avant de calculer.

Quelques formulations qui fonctionnent bien :

- « Quelles contreparties concentrent le plus d'exposition ? Donne le top 5 avec le montant en EUR. »
- « Y a-t-il des anomalies dans les trades d'avril (prix hors marché, dates de jours fériés, contreparties inconnues) ? »
- « Montre l'évolution mensuelle du notionnel par desk sur 2026. »
- « Quelle est la date de règlement T+2 d'un trade du 30 avril 2026 ? » — les calendriers
  TARGET2, Londres et New York sont intégrés (jours fériés, fins de mois ouvrées).
- « Fais-moi un rapport PDF sur l'activité de trading du premier trimestre, avec un graphique et le détail par desk. »

## 3. Lire une réponse

**Les chiffres sont sourcés.** Une référence comme **[#4]** renvoie à l'étape n° 4, c'est-à-dire
à l'appel qui a produit le chiffre. Cliquez sur les étapes au-dessus de la réponse pour voir la
requête exacte et ce qu'elle a renvoyé.

Sous la réponse :

- ***based on …*** ouvre la chaîne de preuve : chaque étape avec sa source, sa requête SQL ou
  son code, le nombre de lignes, l'heure, une empreinte SHA-256 du résultat, ainsi que les
  vérifications qui ont tourné (choix des sources, relecture critique, valeurs de filtre
  vérifiées dans la source…).
- ***Explain the method*** rédige la méthode en langage courant à partir de cette chaîne.
- ***Audit trail*** télécharge tout cela en Markdown ou en JSON, pour un contrôle ou un audit.
- ***from N sources*** liste les sources extérieures lues pendant la réponse.

Au survol de la réponse apparaissent le bouton *Copy answer*, le modèle utilisé, le nombre
d'outils appelés, les tokens consommés et le temps jusqu'au premier mot.

**AGENT dit ce qu'il n'a pas pu établir.** Un outil en échec, une donnée absente, une source
injoignable sont signalés tels quels. AGENT ne remplace jamais un résultat manquant par une
valeur plausible.

### Travailler sur les données d'une étape

Ouvrez une étape (clic sur sa ligne) :

- la **requête SQL** ou le **code Python** s'affiche lisiblement, avec un bouton de copie ;
- le **résultat** est un tableau : cliquez sur un en-tête pour trier, tapez dans *Filter*
  pour filtrer, basculez entre nombres formatés et bruts. La dernière ligne totalise les
  quantités (Σ) et fait la moyenne des taux ; survolez-la pour le min, le max et le nombre ;
- **Copy for Excel** copie le tableau prêt à coller dans Excel (nombres reconnus comme
  nombres) ; **Excel** / **CSV** téléchargent **toutes** les lignes du résultat, même au-delà
  de l'extrait affiché, avec l'onglet *Provenance* ;
- **Edit & run** : modifiez la requête (une date, un filtre) et exécutez-la vous-même
  (**⌘/Ctrl+Entrée**), sans passer par l'agent. Seules les sources en lecture le permettent,
  et ces exécutions ne sont pas ajoutées aux preuves de la réponse ;
- **Ask about #N** insère la référence de l'étape dans votre prochaine question
  (« trace #5 par desk »).

Les tableaux écrits par l'agent dans sa réponse ont aussi, au survol, **Copy for Excel** et
**CSV**.

**Chiffres vérifiés.** Sous chaque réponse, *figures verified* signifie que chaque chiffre
significatif de la réponse se retrouve dans le résultat d'une étape. *N figures unverified*
liste ceux qui n'y sont pas — typiquement un total calculé de tête : vérifiez-les avant
usage.

## 4. Graphiques

Demandez-le simplement : « montre », « graphique », « évolution », « répartition »… AGENT trace
un graphique à partir des lignes réellement renvoyées par une requête. Il ne recopie jamais de
chiffres à la main.

Au survol d'un graphique :

| Bouton | Effet |
|---|---|
| *Modify this chart* | décrivez la modification (« en barres empilées », « trie par valeur », « ajoute la ligne de limite à 100 % ») |
| *Data* | les données exactes du graphique, en tableau |
| *Spec* | la spécification Vega-Lite, pour un usage ailleurs |
| *Download PNG* / *Download SVG* | l'image, pour une présentation ou un e-mail |

Cliquer sur un point affiche sa valeur exacte.

## 5. Exporter des résultats

| Vous voulez | Demandez | Vous obtenez |
|---|---|---|
| Un fichier de données | « exporte en Excel », « donne-moi l'extrait en CSV » | un classeur Excel mis en forme et filtrable, ou un CSV / JSON |
| Plusieurs tableaux dans un classeur | « un Excel avec un onglet par desk » | un onglet par tableau |
| Un document à transmettre | « fais un rapport PDF… » | un PDF avec graphiques, tableaux et sources (voir la [section 6](#6-rapports-pdf)) |
| Une réponse telle quelle | le bouton **PDF** sous la réponse | la question, la réponse, ses graphiques, ses sources et l'annexe de provenance |
| Un graphique seul | *Download PNG / SVG* sur le graphique, ou « exporte ce graphique en PDF » | une image, ou un PDF d'une page |
| Toute la conversation | **⌘K** → *Export this conversation* | un fichier Markdown |

Chaque fichier produit apparaît sous la réponse, dans une carte avec **Download** (et
**Open** pour un PDF, qui l'ouvre dans un nouvel onglet). Tous les fichiers de l'espace de travail restent accessibles par
**⌘K** → *Workspace files*.

**Traçabilité des exports.** Un Excel contient un onglet *Provenance*, un CSV ou un JSON est
accompagné d'un fichier `.provenance.json`, et un PDF se termine par une annexe *Méthode et
provenance*. Dans tous les cas, on sait quelle requête a produit quelles lignes.

## 6. Rapports PDF

### Demander un rapport

Il suffit de le dire, dans la même question que l'analyse ou après :

- « Fais-moi un **rapport PDF** sur l'utilisation des limites VaR par desk au 30 avril 2026,
  avec un graphique en barres et le tableau des chiffres. »
- « Exporte ces résultats en PDF. »
- « Mets le graphique et le top 10 des contreparties dans un PDF pour le comité des risques. »

AGENT fait l'analyse, trace les graphiques, puis assemble le PDF. Dans sa réponse, il donne
l'essentiel en une ou deux phrases chiffrées, pour que vous n'ayez pas à ouvrir le fichier pour
connaître la conclusion, et la carte de téléchargement apparaît en dessous.

### Ce que contient le PDF

- Un **en-tête** avec la date, un **titre** et un **sous-titre** qui précise le périmètre
  (sources, période, unités).
- Des **sections dans l'ordre choisi**, chacune pouvant contenir du texte (listes, gras,
  tableaux Markdown), un **graphique** et un **tableau** de résultats. Les graphiques sont ceux
  que vous avez vus à l'écran, rendus en haute définition sur fond blanc.
- Des **tableaux** : les nombres sont alignés à droite, et les en-têtes lisibles (`notionnel_eur`
  devient « Notionnel EUR »). Au-delà de 40 lignes, le tableau est tronqué avec une mention ;
  demandez un Excel pour l'ensemble.
- Les **citations** [#N] du texte deviennent des renvois numérotés vers une page **Sources**.
- Une annexe **Méthode et provenance** : pour chaque résultat utilisé, la source, l'outil, la
  requête exacte, le nombre de lignes et l'empreinte SHA-256.
- Un pied de page avec le titre, la date de génération et la pagination.

**Les chiffres du texte sont vérifiés.** Chaque chiffre significatif écrit dans le rapport
(avec décimales, ou d'au moins quatre chiffres hors années) doit se retrouver dans les
résultats de la conversation. Les arrondis, les pourcentages et les montants abrégés
(« 1,95 M€ ») sont reconnus. Un chiffre introuvable est renvoyé à l'agent pour correction ;
s'il persiste, le rapport est produit avec la mention **À vérifier** juste sous la phrase
concernée.

**La langue suit celle du rapport.** Un rapport rédigé en français a ses libellés en français
(« Page 2 sur 3 », « Méthode et provenance », dates et nombres au format français :
« 27 septembre 2026 », « 1 234 567,89 »). Un rapport rédigé en anglais a ses libellés en anglais.

### Exporter une réponse en un clic

Sous chaque réponse, au survol, le bouton **PDF** (à côté de *Copy answer*) télécharge la
réponse telle qu'elle est : la question en sous-titre, le texte, chaque graphique tracé (à
l'endroit où la réponse le place), les sources numérotées et l'annexe *Méthode et
provenance*. Si vous avez cliqué sur *Explain the method*, cette explication en clair est
ajoutée dans une section *Méthode*. Le modèle n'est pas sollicité : le
PDF est assemblé à partir de ce que la réponse a déjà produit. C'est la voie la plus sûre
avec un petit modèle local, et le fichier est aussi rangé dans l'espace de travail
(**⌘K** → *Workspace files*).

Pour un document structuré (sections choisies, tableaux, mise en avant des conclusions),
demandez plutôt un rapport à l'agent, comme ci-dessus.

### Retoucher un rapport

Continuez la conversation : « ajoute une section sur les dépassements », « remplace le
camembert par des barres », « titre : Revue mensuelle des risques ». AGENT régénère le PDF ; la
version précédente reste dans l'espace de travail, sous un autre nom.

### Bon à savoir

- Les rapports demandés à l'agent sont produits en **mode Agent** uniquement, car le mode
  LLM direct n'a pas les outils de l'application (voir la
  [section 9](#9-mode-agent-ou-llm-direct)). Le bouton **PDF** sous une réponse fonctionne
  dans les deux modes.
- Tout est produit sur le serveur : le PDF et ses graphiques ne passent par aucun service
  extérieur.
- Un rapport enchaîne plusieurs étapes (données, calcul, graphique, assemblage). Avec un
  petit modèle local sur un poste modeste, comptez plusieurs minutes. Si le rapport préparé
  omet le graphique ou le tableau demandé, l'outil le renvoie à l'agent pour qu'il le
  complète. Relisez tout de même la synthèse rédigée par le modèle : les chiffres du tableau
  font foi.
- Un rapport ne contient que des données obtenues pendant la conversation : un graphique ou
  un tableau qui n'a pas été calculé ne peut pas y figurer.

## 7. Pièces jointes

Le trombone (ou un glisser-déposer, ou un collage) joint jusqu'à 8 fichiers de 25 Mo au plus.

- **Images** (PNG, JPEG, WebP, GIF) : lues par le modèle s'il sait voir (capacité *vision*). Sinon,
  AGENT vous prévient que l'image n'a pas été envoyée.
- **Autres fichiers** (CSV, Excel, texte…) : déposés dans l'espace de travail, où AGENT les lit
  et les analyse (« analyse le fichier joint et compare-le aux trades de mars »).

## 8. Garder la main

**Approbations.** Selon le réglage choisi par l'administrateur, une action qui modifie quelque
chose (écriture dans une source, par exemple) vous est présentée avant exécution, avec ses
paramètres : **Allow** pour l'exécuter, **Deny** pour la refuser. Par défaut, seules les
écritures le demandent.

**Corriger une question.** Survolez votre question et cliquez sur *Edit* : modifiez-la puis
relancez. La suite de la conversation repart de cette question.

**Questions de clarification.** Quand AGENT vous propose des choix, cliquez sur l'un d'eux ou
tapez votre propre réponse.

**Faits appris.** Ce qu'AGENT découvre sur une source (une convention, un piège) est proposé à
l'administrateur, qui le valide ou non. AGENT ne se l'apprend pas seul.

## 9. Mode Agent ou LLM direct

La petite icône à côté du trombone bascule entre deux modes :

| | **Agent** (par défaut) | **LLM direct** |
|---|---|---|
| Ce qui répond | l'agent : il choisit les sources, planifie, vérifie, compose | le modèle seul |
| Outils | toutes les sources connectées + les outils de l'application (graphiques, exports, PDF, Python…) | uniquement les serveurs MCP que vous cochez |
| Idéal pour | analyses, rapports, chiffres à justifier | une question simple à une source précise, ou discuter avec le modèle |

En mode direct, le menu liste les serveurs connectés à cocher (*all* / *none*). Le composer
affiche alors `Direct · N`, et chaque réponse porte la mention `direct · <serveurs>`. Le choix
est mémorisé dans votre navigateur. Les approbations, le cloisonnement réseau, le masquage des
secrets et la piste d'audit restent actifs dans les deux modes.

## 10. Conversations, raccourcis, mémoire

| Raccourci | Effet |
|---|---|
| **⌘K** (Ctrl+K) | palette : actions, réglages, conversations, recherche dans leur contenu |
| **⌘⇧K** | historique des conversations |
| **⌘⇧O** | nouvelle conversation |
| **⌘,** | administration (si vous y avez accès) |
| **/** | placer le curseur dans le champ de saisie |

Une conversation reprend là où vous l'avez laissée, même après un rechargement de la page
pendant une réponse.

**Mémoire.** AGENT retient ce que vous lui demandez de retenir (« retiens que nos montants
sont toujours en EUR ») et s'en sert dans les conversations suivantes.

**Procédures enregistrées (skills).** Une procédure récurrente (revue mensuelle, contrôle
qualité…) peut être enregistrée par l'administrateur, puis lancée depuis **⌘K** →
*Run a skill*.

## 11. Pour l'administrateur

L'administration s'ouvre avec **⌘,** ou la petite icône en haut à droite. Elle est accessible
depuis la machine du serveur, ou partout avec le mot de passe d'administration.

| Onglet | Rôle |
|---|---|
| **Model** | serveur de modèles (**Ollama** ou **OpenAI-compatible** : vLLM, LM Studio, llama.cpp…), boutons **List models** et **Test connection**, choix du modèle principal et du modèle rapide |
| **MCP servers** | bibliothèque de serveurs, serveurs connectés, banc d'essai pour appeler un outil à la main |
| **Data sources** | description de chaque source, modèle de données, métriques, requêtes vérifiées |
| **Tools** | outils disponibles et leur usage |
| **Guardrails** | approbations (*Never* / *For writes* / *Always*), exécution Python, limites (nombre d'étapes, durée, fenêtre de contexte) |
| **Identity & skills** | identité de l'agent et procédures enregistrées |
| **Memory** | ce que l'agent a retenu, et les faits en attente de validation |
| **Audit log** | journal chaîné (inviolable) de toutes les actions |
| **Diagnostics** | état du cloisonnement réseau, des serveurs, du modèle |

**Tester un serveur de modèles.** Dans *Model*, choisissez le type, saisissez l'URL (pour un
serveur OpenAI-compatible, elle se termine en général par `/v1`), puis :

- **List models** affiche les modèles proposés par le serveur ;
- **Test connection** vérifie que le serveur répond, que le modèle répond, puis qu'il sait
  **appeler un outil**. Sans cela, l'agent ne peut rien interroger.

**Fenêtre de contexte.** Avec beaucoup de sources connectées, les instructions et les outils
occupent une large part de la fenêtre du modèle. AGENT allège automatiquement le prompt quand
il ne tient plus, mais un modèle local gagne à disposer d'au moins 32k tokens : réglez
*Context window (tokens)* dans *Guardrails* si la machine a la mémoire nécessaire.

## 12. Dépannage

**« … cannot call tools — it will answer from memory alone »**
Le modèle choisi ne sait pas appeler d'outils. Choisissez-en un marqué *tools* dans
Admin → *Model*, ou vérifiez-le avec **Test connection**.

**« The model was cut off mid-turn… »**
La fenêtre de contexte est pleine, même après l'allègement automatique du prompt. Connectez
moins de serveurs MCP, ou augmentez la fenêtre dans Admin → *Guardrails* →
*Context window (tokens)*.

**J'ai demandé un PDF et je n'ai pas de fichier.**
Vérifiez que vous êtes en mode **Agent** (l'icône à côté du trombone ne doit pas afficher
`Direct`). Si l'analyse n'a pas abouti (données introuvables, requête en échec), AGENT le dit
plutôt que de produire un rapport vide : précisez la source ou la période, puis redemandez
« fais le PDF ». Si la réponse elle-même vous convient, le bouton **PDF** sous la réponse
l'exporte directement, sans repasser par le modèle.

**Un graphique ou un chiffre semble faux.**
Ouvrez l'étape citée ([#N]) pour voir la requête et son résultat, ou cliquez sur
*Explain the method*. Corrigez ensuite la question (*Edit*), en précisant la définition voulue.

**Un serveur MCP n'apparaît pas dans le mode direct.**
Seuls les serveurs **connectés** sont proposés. Leur état et leurs messages d'erreur se lisent
dans Admin → *MCP servers*.

**« … is outside the private network »**
Le déploiement est cloisonné (air-gap) : un modèle, un serveur ou une URL hors du réseau
interne est refusé par construction. Voyez votre administrateur si la ressource est bien
interne. Le domaine peut alors être déclaré dans `AGENT_INTERNAL_DOMAINS`.
