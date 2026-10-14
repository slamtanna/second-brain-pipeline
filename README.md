# vault-pipeline

Unattended daily ingest for the second-brain vault (`~/Documents/Life`).
Each morning it ingests whatever is in `raw/clippings/` (clippings, PDFs,
images), integrates and lints the result, checks it against the existing
wiki, proposes IV15 follow-ups, and emails a 5-bullet digest.

```
inbox items ─► one ingest sub-agent per item, in parallel   (claude -p)
            ─► merge agent, phase A: reconcile · index.md · log.md · lint-fix
            ─► HARD GATE: vp_lint.py, run by the orchestrator itself
                 not clean → merge agent gets the output and retries (×2)
                 still not clean → vault rolled back, items back in inbox,
                 failure email
            ─► merge agent, phase B (same session, vault read-only to it):
                 contradictions with citations · IV15 milestone candidates
                 · digest
            ─► orchestrator appends the run entry to log.md and the
               candidates to ~/Projects/iv15/LOG.md, then emails the digest
```

## Files
| File | What it is |
|---|---|
| `vp_run.py` | The orchestrator. It handles locking, the snapshot and rollback, the fan-out, moving clippings, the gate, log appends and email. |
| `vp_lint.py` | The structural lint, zero LLM, and the gate. `python3 vp_lint.py ~/Documents/Life` works on its own too. |
| `prompts/ingest.md` | The per-item sub-agent prompt. CLAUDE.md's INGEST steps 1-6b, plus concurrency rules and a JSON manifest. |
| `prompts/merge_a.md`, `merge_b.md` | The merge agent's two phases. |
| `run_daily.sh` | The launchd entry point (`--probe` checks access, auth and Gmail). |
| `setup.sh` | Install, probe, test-email, runnow, dry-run, status and uninstall. |

## Setup
```
cd ~/Projects/vault-pipeline
./setup.sh install            # daily 06:30; --at HH:MM --email ADDR --model NAME
./setup.sh test-email
./setup.sh dry-run
```
`install` ends with a probe run from inside launchd, because launchd is
where things break. If it reports `vault-access FAIL`, give `/bin/bash`
Full Disk Access (System Settings → Privacy & Security). macOS blocks
background jobs from `~/Documents` otherwise.

Settings live in `~/.local/state/vault-pipeline/config.env`:
`VP_EMAIL`, `VP_MODEL` (empty = your CLI default), `VP_PARALLEL` (4),
`VP_AGENT_BUDGET` / `VP_MERGE_BUDGET` (USD cap per claude call, 10 / 25),
`VP_EMAIL_QUIET` (1 = email on empty days too; default off),
`VP_GATE_RETRIES` (2), `VP_MAX_FAILS` (2).

## What each stage may touch
| Stage | Tools | Can write |
|---|---|---|
| Ingest sub-agents | Read, Write, Edit, Glob, Grep. No Bash, no MCP, no web. | The vault (wiki pages only, by instruction) and its own manifest. A change under `raw/` fails the run. |
| Merge phase A | The above plus Bash, limited to the lint script. No MCP. | The vault. |
| Merge phase B | Read, Glob, Grep, plus Write/Edit only inside the run dir (`dontAsk` mode). | Only its run dir. The orchestrator also hashes the vault before and after and reverts any change. |
| Email | Only `mcp__claude_ai_Gmail__send_message`. | Nothing. |

Clippings are untrusted web content, so the agents that read them have no
Bash, no network, and no Gmail.

## Rules the run follows
- **All or nothing.** Sub-agents share pages (Michael Burry, hubs), so one
  agent's partial work can't be separated from another's. If any
  sub-agent fails, the run rolls back and re-ingests only the items that
  succeeded. An item that fails twice is skipped until you ingest it by
  hand or run `vp_run.py --reset-failures`. Skipped items are named in
  the email.
- **The gate is the orchestrator's own lint run.** The merge agent's
  "GATE-READY" doesn't count.
- **Claim tags.** New and changed text must carry `[src]` or `[bg]`
  (CLAUDE.md "Claim provenance tags"). The lint diffs the vault against
  the run's snapshot, so only the run's own text is checked. Old pages
  aren't retro-tagged.
- **Frontmatter.** Source pages must have `date`, `author` and
  `clipping`, which matches the vault today. Concept, company and people
  pages have no frontmatter, so requiring it is opt-in:
  `VP_FRONTMATTER=all` once they've been backfilled.
- **IV15 is propose-only.** Candidates are appended to the IV15 repo's
  `LOG.md` as a dated `Milestone candidates` entry, uncommitted, and
  nothing in the repo is changed. The daily screen commits only its own
  files, so it won't sweep this in. Your next IV15 session will.
- **Quiet days** (empty inbox, clean lint) cost nothing: no LLM call and
  no email.

## Where things land
- `~/.local/state/vault-pipeline/runs/<run-id>/` holds everything about a
  run: the pre-run snapshot, the manifests, every claude call's JSON, the
  lint before and at the gate, `contradictions.md`,
  `milestone_candidates.md`, `digest.md`, `email.txt` and `summary.json`.
  The last 30 runs are kept.
- `runs.log` has one JSON line per run. `daily.log` holds the launchd
  output. `failures.json` has the per-item failure counts.
- Every claude session is saved, so you can inspect one with
  `claude --resume <session_id>` (the ids are in `calls/*.json`).
