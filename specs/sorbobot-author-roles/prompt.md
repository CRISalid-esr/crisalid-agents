# Sorbobot : ne compter que les publications dont le chercheur est l'auteur

Ce travail se fait dans deux dépôts à la fois : `crisalid-ai-skills` et `crisalid-agents`. Dans les deux, il faut
travailler sur la branche `sorbobot-author-roles`. Dans `crisalid-ai-skills`, elle part de `dev-main`. Dans
`crisalid-agents`, elle part de `22-sorbobot-implementation`.

## Contexte

Dans le toolset `sorbobot` de `crisalid-ai-skills`, quatre outils ne gardent que les contributions qui ont le rôle
`aut`, `ths` ou `spk` : `list-concept-expert-evidence`, `get-person-publication-counts`,
`list-person-research-concepts` et `list-person-top-documents`. La liste des rôles est écrite en dur dans la requête
Cypher.

Donc une thèse dirigée (`ths`) ou un exposé (`spk`) compte comme une publication du chercheur. Ceux qui ont
dirigé beaucoup de thèses remontent dans le classement des experts, et leur nombre de publications se retrouve trop élevé.
À l'inverse, le rôle `ctb` n'est jamais pris en compte, alors que c'est le rôle mis quand la source ne précise pas
le rôle de la personne.

L'outil `publications-by-theme` avait le même problème. Il a été corrigé dans le commit `bf177d5` (PR #38) avec un
nouveau paramètre `roles` : une liste de codes de rôles séparés par des virgules, par exemple `aut,ctb`. Une valeur
vide garde tous les rôles.

## À faire dans crisalid-ai-skills

Ajouter le même paramètre `roles` aux quatre outils et filtrer de la même façon que dans `publications-by-theme`.
Comme pour cet outil, la valeur par défaut est `""` : un appel sans `roles` garde tous les rôles. Faire le
changement dans `tools.yaml` et dans `tools-auth.yaml`.

Quelques détails :

- `list-person-top-documents` renvoie les rôles de la personne sur chaque document. Il ne doit renvoyer que les rôles
  demandés.
- Mettre à jour les descriptions des outils, qui parlent encore de `aut`, `ths` et `spk`.

Adapter les tests existants dans `tests/test_mcp_toolbox_sorbobot_tools.py`.

## À faire dans crisalid-agents

Le choix des rôles se fait dans le code de Sorbobot, pas dans la toolbox. Sorbobot doit toujours passer `roles` aux
quatre outils :

- `aut,ctb` pour la recherche d'experts, le nombre total de publications et l'expertise d'un chercheur ;
- `aut,ctb,ths,spk` pour le profil, qui affiche les thèses dirigées et les exposés dans leurs propres sections.

Un document où le chercheur a le rôle `ctb` doit être traité comme une publication dont il est l'auteur.

Mettre à jour les tests de Sorbobot, et la spec `specs/22-sorbobot-implementation/prompt.md` partout où elle parle
des rôles.
