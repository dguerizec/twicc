---
name: twicc-whoami
description: Return the details of the session that owns the calling process. Use to discover your own TwiCC session_id from inside a Bash tool, when you need to reference your own session (e.g. for related-command filtering).
---

# twicc-whoami

Retrouve la session TwiCC dont **tu es** le processus. Utile quand un agent a
besoin de son propre `session_id` mais ne l'a pas en contexte.

## Mécanisme

`twicc whoami` remonte la chaîne des PID parents depuis le processus courant
jusqu'au PID 1, et cherche une correspondance parmi les `ProcessRun.agent_pid`
des sessions vivantes. Si une correspondance est trouvée, la commande affiche
les mêmes informations que `twicc session <ID>` — id, provider, titre,
project_id, coûts, paramètres, cycle de vie, etc.

Si aucune correspondance n'est trouvée (tu l'as lancé depuis un terminal
ordinaire, pas depuis le Bash tool d'un agent), la commande sort avec le code 1
et un message clair.

## Invocation

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory".

```bash
$TWICC whoami           # sortie lisible (JSON indenté)
$TWICC whoami --json    # JSON compact, adapté au parsing
```

## Codes de sortie

| Code | Signification |
|---|---|
| 0 | Session résolue ; détails affichés sur stdout |
| 1 | Aucune session dans l'arbre PID (aussi : lancé depuis un shell ordinaire) |

## Utilisation typique

```bash
# Je suis un agent ; quel est mon TwiCC session_id ?
MY_SESSION_ID=$($TWICC whoami --json | jq -r .id)
```

## Commandes associées

Pour lister ou filtrer les sessions que TU as créées, préfère le flag dédié
`--spawned-by self` sur `twicc sessions`, `twicc processes` et `twicc search` —
pas besoin d'appeler `whoami` au préalable, le flag résout lui-même la session
courante sous le capot.
