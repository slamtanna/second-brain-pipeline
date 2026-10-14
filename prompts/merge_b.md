The orchestrator re-ran the structural lint independently and it is
**CLEAN**, so the gate passed. This is **phase B**. The vault is now
read-only to you, and so is the IV15 repo. You can write only inside
`{{RUN_DIR}}`. Produce the three files below, and nothing else.

## 1. `{{RUN_DIR}}/contradictions.md`: contradiction check
Compare what this run added (see the manifests and the diff against
`{{SNAPSHOT}}`) against the existing pages. Start with each manifest's
`possible_contradictions`, then go beyond them:
- **Position state.** For every company the new material touches, check
  its `Position history` tail and its index.md line against the newest
  portfolio snapshot among the source pages (a ranking or holdings list
  in a Trading Post). Stale position state is where this wiki's real
  contradictions have been.
- **Claims and figures** on existing pages that the new material
  contradicts or supersedes (a price, a size, a thesis reversed).
- **Post-publication corrections** that the new material records, and
  whether every page carrying the corrected claim got it.
- **Forward-reference language** on the pages touched this run ("to be
  ingested", "not-yet-ingested", "pending", "coming soon", "TBD",
  "promised", "forthcoming", "in progress"). Flag it only where it's now
  stale.

For each conflict write one bullet: the topic in bold; then
`[[Page A]]` says "…" (citing its source page) against `[[Page B]]` or the
new `[[source page]]`, which says "…"; then its status: **new**,
**already recorded on the page**, or **superseded** (a later post settles
it). Quote short exact phrases. Put a separate `Stale position state`
list under its own heading if there is any. List only. Don't fix. If
nothing conflicts, write `No contradictions found.` and one line on
what you checked.

## 2. `{{RUN_DIR}}/milestone_candidates.md`: IV15 scan
The IV15 repo is at `{{IV15}}`. Read its CLAUDE.md and PLAN.md. In
PROMPTS.md read the latest milestone, its acceptance criteria, and
"Ongoing". Also read `data/calibration_iv15.json`, `data/quality.json`,
`data/trade_log.json`, the zone and tier-bar constants in
`src/screen.py`, and the tail of LOG.md. Grep LOG.md for
`Milestone candidates` so you don't re-propose something already there.

Scan the new material (and the manifests' `iv15_signals`) for anything
that affects IV15:
- **zones**: the P/IV15 buy zone, the tier bars, or anything Burry says
  about where he buys;
- **tiers**: a Fortress/Castle/Chapel/Stone/Wood rating, given or changed;
- **calibration data**: a published IV15 figure, a bound, a ΔE or SBC
  figure, or a ranking;
- **trades and labels**: entries for `trade_log.json`'s `post_window`;
- **acceptance criteria**: anything that would change how a current or
  planned milestone should be judged;
- **method**: a newly published piece of his valuation method.

Write one bullet per proposed item:
`- **<area>**: <the proposed change, concretely> — evidence: "<exact
figure or quote>" ([[<source page>]], <date>) — why: <what it would move
in IV15: which file, test, metric or criterion> — size: routine per-post
update | milestone-scale`.

**Propose only.** Don't edit the repo. The orchestrator appends your
file to LOG.md under a "Milestone candidates" heading. If nothing in the
new material touches IV15, the whole file is the single word `NONE`.

## 3. `{{RUN_DIR}}/digest.md`: the email
Exactly **5 bullets**, plain text, each line starting `- `, with no
heading, preamble or sign-off. Together they answer "what changed in my
vault today and why it matters". Lead each bullet with the change, then
say why it matters to me: thesis, positions, IV15. Most important first.
Spend a bullet on contradictions or IV15 candidates when there are any.
Mention pipeline health only if something went wrong or was skipped
(gate retries, failed items: {{FAILED_SHORT}}). Each bullet is at most
45 words. Don't use wikilinks; use page titles in plain text.

Reply with one line: `PHASE-B-DONE`.
