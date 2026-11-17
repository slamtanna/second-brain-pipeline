# second-brain-pipeline

An unattended pipeline that turns a day's saved reading into a linked personal wiki, then emails a short digest of what changed.

I keep a "second brain": a folder of Markdown pages (people, companies, concepts and sources, cross-linked with `[[wikilinks]]`) built mostly from long-form investing research. Filing a new article properly takes real work: write a source page, update every company and person it touches, notice where it contradicts what the wiki already says, and keep the index consistent. This pipeline does that every morning without me, using Claude Code agents in headless mode. I read the result as a phone-sized email.

## The design problem: letting agents write to something you care about

Having an LLM summarize an article is easy. The hard part is letting several agents edit a knowledge base unattended, every day, without slowly corrupting it. The design follows from that:

- **Least privilege per stage.** Saved web pages are untrusted input, so the agents that read them get file tools only: no shell, no network, no email. The digest-writing phase can read the wiki but write only to its own scratch folder. Only the final step can send email, and it can do nothing else.
- **Deterministic where possible.** A plain Python orchestrator, not an agent, handles locking, snapshots, rollback, moving files, logging and the email. Agents do only the parts that need judgment.
- **An independent quality gate.** After the agents finish, a zero-LLM lint (`vp_lint.py`) checks the wiki's structure: broken links, orphaned sources, missing frontmatter, index drift. The orchestrator runs it itself; an agent saying "done" doesn't count. If the wiki fails, the merge agent gets two retries to fix it.
- **All or nothing.** The wiki is snapshotted before each run. If any step fails, everything rolls back and the articles go back in the inbox. An article that fails twice is set aside and named in the email.
- **Every claim is labeled.** New text must mark each claim as coming from the source (`[src]`) or from background knowledge (`[bg]`), and the lint enforces this on the run's changes only.
- **Read-only means read-only.** The vault is hashed before and after the read-only phase, and any change is reverted.
- **Bounded cost.** Each agent call has a dollar cap, and a day with nothing to file makes no LLM calls and sends no email.

**Stack:** Python standard library only, the Claude Code CLI (`claude -p`) for the agents, macOS `launchd` for scheduling, and Gmail through a Claude connector for the digest. About 1,400 lines of Python plus three prompt files.

It's a personal tool: paths assume my vault's layout, and it also feeds follow-up candidates to a separate private research project (IV15). The rest of this README is the operator manual.

---

## Overview

Each morning it ingests whatever is in `raw/clippings/` (clippings, PDFs,
images), integrates and lints the result, checks it against the existing
wiki, proposes IV15 follow-ups, and emails a digest (a headline, a
bottom line and a few short labelled sections).

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
| `vp_frontmatter.py` | Derives the concept/company/person frontmatter from each page. `--apply` writes it, `--show N` previews it, and with no flag it lists the pages out of sync. |
| `vp_lint.py` | The vault's one structural lint, zero LLM: the pipeline's gate, and also the vault's Stop hook, through the shim `~/Documents/Life/.claude/hooks/wiki_lint.py` (its `--mark` and `--stop` modes call `vp_lint.hook()`). `python3 vp_lint.py ~/Documents/Life` works on its own too. |
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
- **Sub-agents get a pipeline-aware Stop hook.** `vp_run.py` sets
  `VP_HOOK_ROLE=ingest` and `VP_SNAPSHOT`, and the vault's hook skips the
  checks that are the merge agent's job (index, log, orphans, the clipping
  not having moved yet). It adds the claim-tag check, so untagged claims
  are fixed by the agent that wrote them. Interactive sessions never set
  the role, so for them the hook is unchanged.
- **The gate is the orchestrator's own lint run.** The merge agent's
  "GATE-READY" doesn't count.
- **Claim tags.** New and changed text must carry `[src]` or `[bg]`
  (CLAUDE.md "Claim provenance tags"). The lint diffs the vault against
  the run's snapshot, so only the run's own text is checked. Old pages
  aren't retro-tagged.
- **Frontmatter.** Source pages have hand-written `date`, `author` and
  `clipping`. Concept, company and person pages carry derived
  frontmatter (`type`, `ticker`, `hub`, `sources`, `first_source`,
  `last_source`) computed by `vp_frontmatter.py` from the page itself.
  The merge agent's lint wrapper and the gate re-sync it, so no agent
  maintains it by hand. The lint requires it on every page; pass
  `--frontmatter sources` to relax that.
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
  `milestone_candidates.md`, `digest.md`, `email.txt`, `email.html` (open it
  in a browser to preview the email) and `summary.json`.
  The last 30 runs are kept.
- `runs.log` has one JSON line per run. `daily.log` holds the launchd
  output. `failures.json` has the per-item failure counts.
- Every claude session is saved, so you can inspect one with
  `claude --resume <session_id>` (the ids are in `calls/*.json`).
