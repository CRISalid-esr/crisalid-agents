# Contributor roles in TRM and the generic agent — specification

## 1. Problem

The TRM batch (`specs/topic-matching/prompt.md` §5.2) credits every contributor of a retrieved publication with the
publication's score. `publications-by-theme` returns all the persons linked to a document, whatever their role, and
`trm/scoring.py` keeps only `{uid, name}`: a thesis advisor, a committee member, a rapporteur or a jury president is
scored as if they had written the thesis. With `internal_only`, the effect is stronger: the doctoral student is
often external, so the advisor is the only contributor left.

The generic agent has the same flaw in another form: its prompts send every thematic question to
`publications-by-theme`, and the LLM then presents all the contributors of a document as its authors.

The role vocabulary and its groups (authorship, editorial, thesis supervision and jury) are described in
`crisalid-ai-skills/specs/publications-by-theme-roles/prompt.md` §1.

## 2. Principle

`crisalid-ai-skills` (branch `publications-by-theme-roles`) adds a `roles` allow-list parameter to
`publications-by-theme` and describes the role codes in the tool metadata. The two consumers of this repository use
it differently:

| Consumer | Who fills `roles` | Where the policy lives |
|---|---|---|
| TRM batch | code | configuration (`TRM_CONTRIBUTOR_ROLES`), §3–4 |
| Generic agent | the LLM, per question | a rule in the two system prompts, §5; the vocabulary stays in the tool metadata |

In TRM the LLM is not involved: it does not call the tool, the rule does not depend on the topic, and filtering
after the pre-score would leave advisors in the candidate slots. Nothing is added to `trm/expansion_prompt.md` or
`trm/verification_prompt.md`.

## 3. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TRM_CONTRIBUTOR_ROLES` | `aut,ctb` | Comma-separated LoC relator codes a contribution must hold to count; empty = every role (previous behaviour) |

- `aut` is the author (thesis authors included, they are stored as `aut`). `ctb` is not a role in itself: it is the
  fallback the IKG writes when a source gives no role or an unrecognised one. It is kept so that people whose role
  is unknown are not lost.
- Editorial roles (`edt`, `pbd`, `sad`, `aui`) and thesis supervision (`ths`) are left out by default and can be
  added per deployment.

`TRMSettings.contributor_roles: tuple[str, ...]`, parsed in `from_env` (trimmed, lower-cased, empty items dropped)
and included in `to_dict()` so the JSON report records the list of the run. The Markdown / HTML report header adds
"roles `aut, ctb`" after the model.

## 4. Retrieval (`trm/sources.py`)

- `ToolboxResearcherSource(client=None, roles=())`; `scripts/topic_researcher_match.py` / `trm/runner.py` pass
  `settings.contributor_roles`.
- At toolset load, `supports_roles` is detected from the tool's `args_schema`, as `supports_internal_only` is.
  When supported and the list is not empty, the call adds `roles=",".join(roles)`.
- `roles_filter(rows, roles)`, next to `internal_only_filter` and always applied (idempotent when the tool already
  filtered): keeps the contributors with at least one role whose last URI segment is in the list, drops the
  documents left without contributor. A contributor without roles is dropped when the list is not empty; an empty
  list returns the rows unchanged.
- Order: `roles_filter`, then `internal_only_filter`.

With a toolbox that lacks the parameter the filter still holds, but the 100 documents of a query include those kept
only for a non-author contributor, so fewer useful documents remain. A warning is logged once per run in that case.

`trm/scoring.py` is unchanged: it receives contributors that are already filtered.

## 5. Generic agent (`agents/generic_agent/`)

The tool metadata reaches the LLM as it is (the toolset is bound to the model), so the parameter and its hints are
visible without any code change. That is not enough: an optional parameter with an empty default is usually left
out, and both prompts tell the LLM to "always call publications-by-theme first" for "what a researcher works on,
which lab covers a field" without a word about roles. The prompts already carry per-parameter rules for this tool
("Always set `use_abstract` to true"); the role rule joins them.

Added to `mcp_toolbox_restricted_prompt.md` and `mcp_toolbox_unrestricted_prompt.md`, after the
`publications-by-theme` rule:

```
- **The people attached to a publication hold different roles**, given as Library of Congress relator codes. The most frequent ones: author (`aut`), degree committee member (`dgc`), thesis advisor (`ths`), opponent (`opn`), rapporteur (`rap`), publication director (`pbd`), jury president (`pra`), scientific advisor (`sad`), editor (`edt`), author of introduction (`aui`). Being listed on a publication does not make someone its author. `ctb` is not a role in itself: it is the fallback recorded when the source does not say which role the person held.
- When the question is about who works on, wrote on or is an expert of a subject, call publications-by-theme with `roles` set to "aut,ctb": the authors, plus the people whose role is unspecified. Widen the list only when the question asks for it (e.g. "ths" for thesis supervision, "edt" for edited volumes); the tool description lists the codes.
- When you present a person whose role is not author, name the role (thesis advisor, committee member, editor…) instead of calling them an author; for `ctb`, do not state a role.
```

- The first rule names the ten most frequent roles with their codes, so that the LLM can read the roles returned
  by any tool and choose a list without relying on the tool description alone; the tool metadata remains the
  reference for the vocabulary and its grouping.
- No default is injected by the agent code when the LLM omits `roles`: questions such as "who supervised theses on
  X?" must stay answerable, and a silent default would contradict what the LLM believes it asked for.
- The first rule states the vocabulary in terms of people and roles, and never uses "contributor" for the people
  attached to a publication: in the LoC vocabulary that word is the `ctb` fallback.
- The third rule also covers the calls made without filter and the other tools returning roles
  (`get-publication`).
- With a toolbox that lacks the parameter, the rule is harmless: the LLM does not see `roles` in the schema and
  only the presentation sentence applies.
- The `mcp-toolbox` scaffold template has no toolset-specific prompt and is not changed.

## 6. Documentation

`.env.sample`, the environment table of `CLAUDE.md` and of `README.md` (`TRM_CONTRIBUTOR_ROLES`). Earlier
specifications are not edited: this file is the reference for the change.

## 7. Tests (offline)

- `roles_filter`: URI and short forms, several roles on one contribution, contributor without roles, document left
  empty, empty list;
- settings: default, parsing of `TRM_CONTRIBUTOR_ROLES`, empty value;
- `ToolboxResearcherSource` with a fake client: `roles` sent only when the schema has the parameter, client-side
  filter applied in both cases;
- pipeline end to end with fakes: a person who is only `ths` on the retrieved documents is not a candidate; the
  same person becomes one with `contributor_roles=("aut", "ths")`;
- generic agent: both prompt files contain the role rule (the behaviour of a real LLM is not tested offline; it is
  checked by hand on a thesis-heavy theme before the PR is merged).

## 8. Branches and commits

`crisalid-agents`, branch `trm-contributor-roles`:

0. `docs: contributor roles spec` — this file
1. `feat(trm): contributor roles allow-list` — settings, source, report header, tests, documentation
2. `feat(generic_agent): contributor roles rule in the system prompts`

Paired with `crisalid-ai-skills`, branch `publications-by-theme-roles`. Either side can be deployed first; the
generic agent only filters once the toolbox has the parameter.

## 9. Open questions

- `pbd`: its source mapping decides whether scientific editors of collective
  volumes join the default list.
- Sorbobot counts `aut`, `ths` and `spk` as expertise evidence (hard-coded in its toolbox tools). TRM's default is
  stricter on purpose; aligning the two is a separate decision.
