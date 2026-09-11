# Lumen

Un **super-agent IA autonome, entièrement local**. Une seule barre de saisie sur un écran
vide. Derrière : un modèle Ollama qui tourne sur votre machine, une boucle agentique
bornée qui raisonne, appelle ses outils et se corrige, et une bibliothèque de serveurs MCP
connectables en deux clics depuis une page d'administration discrète.

Rien de ce que vous écrivez ne quitte l'ordinateur, sauf si vous connectez explicitement un
outil qui sort (la recherche web, un serveur MCP distant). Il n'y a aucune clé d'API à
fournir pour démarrer, et aucune donnée de démonstration nulle part : ce que l'interface
affiche est ce que les outils ont réellement renvoyé.

---

## Démarrage

Prérequis : **Python 3.12+**, **Node 20+**, et **[Ollama](https://ollama.com)** avec au
moins un modèle qui sait appeler des outils.

```bash
ollama pull qwen3.5:4b     # outils + raisonnement + vision, 3,4 Go
make install
```

Puis, dans deux terminaux :

```bash
make api    # http://localhost:3041
make web    # http://localhost:3040
```

Ouvrez `http://localhost:3040`, puis **⌘,** → *Modèle* et choisissez-en un. C'est tout.

En production, un seul processus suffit — le backend sert l'interface compilée sur la même
origine, donc aucun CORS à configurer :

```bash
make serve   # construit le frontend puis sert tout sur :3041
```

### Choisir le modèle

L'administration liste les modèles de votre serveur Ollama avec **les capacités déclarées
par le serveur**, jamais devinées d'après le nom :

| Capacité | Sans elle |
|---|---|
| `tools` | l'agent ne peut appeler **aucun** outil ; il répond seulement de mémoire |
| `thinking` | pas de trace de raisonnement affichée |
| `vision` | les images jointes ne sont pas envoyées — et Lumen vous le dit |

Testé sur une machine à 9 Go de RAM avec `qwen3.5:4b` (les quatre capacités, 262k de
contexte). `gemma4:e4b` convient aussi. Un modèle sans `tools` reste sélectionnable — avec
un avertissement explicite, pas un échec silencieux.

---

## Ce que l'agent sait faire sans rien configurer

| Outil | Ce qu'il fait réellement |
|---|---|
| `web_search` | recherche DuckDuckGo, sans clé d'API |
| `web_fetch` | récupère une URL et la lit (HTML → texte, JSON, CSV) |
| `run_python` | exécute du Python dans un processus séparé, tué au-delà du délai |
| `read_file` / `write_file` / `list_files` | l'espace de travail de Lumen, et rien d'autre |
| `remember` / `recall` | mémoire durable d'une conversation à l'autre |
| `plan` | le plan que vous voyez se cocher en direct |
| `current_time` | date et heure de la machine |

Tout serveur **MCP** connecté ajoute ses outils au même index, préfixés par le nom du
serveur (`filesystem__read_file`). La bibliothèque propose quinze recettes prêtes à
l'emploi — système de fichiers, Git, SQLite, Postgres, Playwright, GitHub, Slack, Notion,
Context7… — et n'importe quel autre serveur s'ajoute par le formulaire « serveur
personnalisé », en stdio ou en HTTP.

---

## Comment fonctionne la boucle

```
message ──► [ raisonner ──► appeler des outils ──► observer ]* ──► répondre
                   ▲                    │
                   └──── critique ──────┘
```

Une boucle ReAct bornée, pas une machine à états à neuf nœuds : neuf appels LLM avant le
premier mot, sur un modèle local, c'est une minute de silence pour répondre « bonjour ».
La rigueur est donc attachée là où elle paye sa latence :

- **La garde anti-boucle** tourne à chaque appel, gratuitement. Elle signe un appel par sa
  *structure* (outil + arguments), jamais par sa formulation — un modèle à qui l'on dit
  « essaie autre chose » repropose la même idée reformulée avec une fiabilité remarquable.
  Les requêtes de recherche sont comparées par ensemble de mots, donc deux recherches aux
  mêmes mots dans un autre ordre comptent comme une seule.
- **Le critique** n'intervient que sur un appel **échoué**, où son conseil change la
  tentative suivante. C'est un appel LLM distinct, qui ne voit que l'appel et son erreur :
  le modèle qui vient d'échouer est le plus mal placé pour juger s'il a réussi.
- **La vérification finale** tourne une fois, avant de répondre, et ne se contente pas de
  suggérer : elle **nomme l'appel d'outil manquant, et c'est l'orchestrateur qui
  l'exécute**. Un modèle à qui l'on suggère d'ouvrir une page répond « je devrais consulter
  cette page » ; un orchestrateur qui l'ouvre ramène la donnée.
- **La synthèse finale** est écrite à partir d'un digest des preuves — la question, puis ce
  que chaque outil a réellement renvoyé — jamais à partir du transcript entier. Rejouer
  quinze mille tokens de sortie d'outils fait répondre un petit modèle à la dernière page
  lue plutôt qu'à la personne, souvent dans la langue de la page.
- **Le plan est exclu des preuves.** Une étape planifiée n'est pas une étape faite : un
  modèle à qui l'on montre son propre plan comme « preuve » rapporte chaque étape comme
  accomplie, et c'est ainsi qu'une exécution qui n'a écrit aucun fichier finit par affirmer
  l'avoir écrit *et vérifié*.

### Honnêteté avant complétude

Le principe qui prime sur tous les autres : **un échec doit rester visible**. Un outil qui
tombe le dit, un serveur qui refuse de démarrer affiche sa propre sortie d'erreur sur sa
carte, une capacité absente est annoncée avant d'être utilisée. Nulle part Lumen ne
substitue une valeur plausible à un résultat qu'il n'a pas obtenu.

---

## Contrôle humain

Trois niveaux, réglables dans *Garde-fous* :

| Mode | Effet |
|---|---|
| Jamais | l'agent agit seul |
| **Pour les écritures** (défaut) | un outil MCP qui modifie quelque chose demande votre accord, dans le fil |
| Toujours | chaque outil est soumis à approbation, exécution Python comprise |

Les outils natifs n'écrivent que dans l'espace de travail de Lumen, donc ils ne sont
soumis à approbation qu'en mode « toujours » : demander d'approuver chaque `run_python`
rendrait l'agent inutilisable au moment précis où il sert le plus. Un serveur MCP de
confiance peut recevoir une approbation automatique, serveur par serveur.

`run_python` mérite d'être nommé pour ce qu'il est : un garde-fou contre l'emballement et
l'accident — processus séparé, répertoire de travail borné, surveillance qui tue le
processus au-delà du délai — **pas un bac à sable de sécurité**. Le code s'exécute avec les
droits de Lumen. L'interrupteur est dans *Garde-fous*.

---

## Administration

Discrète : **⌘,** ou la petite icône en haut à droite.

- **Sans mot de passe** (défaut) — accessible uniquement depuis la machine qui exécute
  Lumen. C'est le bon réglage pour un agent personnel : rien à inventer avant de
  configurer, et rien d'exposé si le port venait à être ouvert.
- **Avec `LUMEN_ADMIN_PASSWORD`** — exigé partout, y compris en local, en échange d'un
  jeton. Les tentatives échouées sont freinées.

Les secrets saisis (jetons, chaînes de connexion) ne reviennent jamais en clair vers le
navigateur : ils sont masqués, et renvoyer une valeur masquée ne l'écrase pas.

Onglets : **Modèle** · **Serveurs MCP** (bibliothèque, serveurs connectés, banc d'essai
pour appeler un outil à la main) · **Outils** · **Garde-fous** · **Mémoire** ·
**Diagnostic**.

---

## Architecture

```
backend/app/
├── main.py              FastAPI ; sert le SPA compilé en production
├── config.py            un préfixe d'env, tout par défaut vide/désactivé
├── deps.py              conteneur de dépendances ; `settings` est une vue *vivante*
├── store.py             JSON plat, écritures sérialisées et différées
├── security.py          accès local seul, ou mot de passe → jeton
├── llm/provider.py      Ollama : streaming, raisonnement séparé, outils natifs
├── mcp/                 client JSON-RPC écrit à la main (stdio + HTTP), registre, catalogue
├── agent/
│   ├── runner.py        la boucle, les approbations, le critique, la synthèse
│   ├── guard.py         la garde anti-boucle
│   ├── prompts.py       tout ce que le modèle sait de lui-même
│   ├── builtin.py       les outils natifs
│   └── memory.py        mémoire longue, rappel par recouvrement pondéré
└── tools/               web, exécution de code, fichiers
frontend/src/
├── App.tsx              les deux états : toile vide, puis conversation
├── api.ts               le client typé unique (+ flux SSE reconnectant)
└── components/          ui, Markdown, Composer, Thread, Sidebar, Admin, McpLibrary
```

Le client MCP est **écrit directement contre le protocole** (JSON-RPC 2.0, version
`2025-06-18`), sans SDK : un seul chemin de code pour un processus local ou un endpoint
distant, et une panne de connexion qui remonte comme un diagnostic lisible — la sortie
d'erreur du serveur comprise — plutôt qu'en erreur d'import opaque.

### Détails qui comptent

- **La fenêtre de contexte est dimensionnée à partir du modèle**, pas laissée au défaut
  d'Ollama. Ce défaut est de 4096 tokens quel que soit le modèle ; le prompt d'un agent —
  instructions plus schémas d'outils — en occupe l'essentiel, et le modèle se fait couper
  au milieu d'une phrase, tour après tour, jusqu'à épuisement du budget. Lumen demande la
  fenêtre déclarée par le modèle, **plafonnée à 16k** : au-delà, le cache d'attention
  chasse le modèle du GPU et divise la vitesse par plusieurs sur une machine à faible
  mémoire. Réglable dans *Garde-fous*.
- **Une exécution survit à l'onglet.** Elle tourne côté serveur ; le flux SSE est une *vue*
  sur elle, réattachable, qui rejoue ce qui a été manqué depuis le dernier numéro de
  séquence reçu. Fermer l'onglet en pleine réponse ne perd rien.
- **Les deltas sont regroupés par frame** avant d'entrer dans l'état React : un modèle
  local rapide transformerait sinon chaque token en re-rendu complet du markdown.
- **Le rendu markdown est sans dépendance** et tolère un document en cours d'écriture — un
  bloc de code non refermé, un tableau à moitié écrit. Aucun `dangerouslySetInnerHTML` :
  la sortie du modèle ne peut injecter aucun balisage.

---

## Limites, dites franchement

- Un modèle de 4 milliards de paramètres se trompe. Les garde-fous rendent ses erreurs
  visibles et rattrapables ; ils ne les suppriment pas. Un modèle local plus gros améliore
  nettement la qualité, au prix de la vitesse.
- `run_python` n'est pas un bac à sable de sécurité (voir plus haut).
- Le store JSON suppose **un seul processus**. Il tient très bien pour un agent personnel ;
  il faudra une vraie base le jour où plusieurs instances écriront en même temps.
- La recherche web passe par le point d'entrée HTML de DuckDuckGo, sans clé. S'il change de
  forme ou limite les requêtes, l'outil **le dit** au lieu de renvoyer une liste vide que le
  modèle interpréterait comme « rien n'existe à ce sujet ».
- Le rappel mémoire utilise un recouvrement de termes pondéré, pas des embeddings :
  inspectable, sans second modèle à charger, et suffisant pour la poignée de faits durables
  qu'accumule un agent personnel.

---

## Ports

`3040` interface · `3041` API. Repli : `3050` / `3051` (`npm run dev:alt`).
