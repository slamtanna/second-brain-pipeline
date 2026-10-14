#!/usr/bin/env python3
"""Structural lint for the second-brain vault. Zero LLM, stdlib only.

This is the pipeline's hard gate: exit 0 only when every error-level check
passes. Warnings are reported but never fail the gate.

Checks (errors):
  broken-link       [[wikilink]] / ![[embed]] that resolves to nothing
  split-link        a [[ opened on a line and never closed on it
  orphan            wiki page with no inbound link from another page
                    (index.md and log.md don't count as inbound)
  index-missing     wiki page not listed in index.md
  index-stale       index.md entry pointing at a missing / non-wiki page
  index-duplicate   wiki page listed more than once in index.md
  index-section     page listed under the wrong index heading
  index-summary     index entry with no one-line summary
  frontmatter       source page missing frontmatter or a required key
                    (date, author, clipping); malformed frontmatter on any
                    page; with --frontmatter all, any page without it
  clipping-missing  source page whose `clipping:` file doesn't exist
  clipping-orphan   processed clipping that no source page claims
  clipping-dup      processed clipping claimed by two source pages
  log-missing       source page with no `## [date] ingest | Title` entry
  provenance        (with --snapshot) text added since the snapshot that
                    carries neither a [src] nor a [bg] claim tag

Usage:
  vp_lint.py VAULT [--json] [--snapshot DIR] [--frontmatter sources|all]

It is also the vault's Claude Code hook. `.claude/hooks/wiki_lint.py` is
a shim that calls hook() below:
  --mark   (UserPromptSubmit) stamp the turn start
  --stop   (Stop) if the vault changed this turn, block once on errors,
           then report them to the user instead of looping

Inside the daily pipeline, vp_run.py sets VP_HOOK_ROLE=ingest (and
VP_SNAPSHOT) for its sub-agents. Their hook then skips the checks that
are the merge agent's job (INGEST_DEFERRED) and adds the provenance check.
"""
import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path

LINK_RE = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
TAG_RE = re.compile(r"\[(src|bg)\]")
SECTION_FOR_DIR = {"people": "People", "concepts": "Concepts",
                   "companies": "Companies", "sources": "Sources"}
SOURCE_KEYS = ("date", "author", "clipping")
# Sections whose content is navigation/attribution/process notes, not claims.
PROVENANCE_EXEMPT_SECTIONS = {"source", "sources", "pages touched", "charts",
                              "images", "see also", "related"}
SKIP_DIRS = {".obsidian", ".git", ".claude", ".gstack", ".trash"}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def strip_code(lines):
    """Yield (lineno, text) with fenced blocks dropped and inline code blanked."""
    in_fence = False
    for i, line in enumerate(lines, 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        yield i, INLINE_CODE_RE.sub("", line)


def split_frontmatter(text):
    """Return (frontmatter dict or None, malformed flag, body start line)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, False, 0
    for j in range(1, len(lines)):
        if lines[j].strip() == "---":
            fm = {}
            for raw in lines[1:j]:
                m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", raw)
                if m:
                    fm[m.group(1)] = m.group(2).strip().strip('"').strip("'")
            return fm, False, j + 1
    return None, True, 0


def parse_target(inner):
    # Tables escape the alias pipe as \| ; both forms split target from alias.
    target = re.split(r"\\?\|", inner, maxsplit=1)[0]
    target = target.split("#", 1)[0].split("^", 1)[0].strip()
    return target


class Vault:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.md = {}          # relpath -> text
        self.by_stem = {}     # lower stem -> [relpath]
        self.by_rel = {}      # lower relpath without .md -> relpath
        self.files_by_name = {}  # lower filename (non-md) -> [relpath]
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                p = Path(dirpath) / fn
                rel = p.relative_to(self.root).as_posix()
                if fn.endswith(".md"):
                    self.md[rel] = p.read_text(encoding="utf-8", errors="replace")
                    self.by_stem.setdefault(fn[:-3].lower(), []).append(rel)
                    self.by_rel[rel[:-3].lower()] = rel
                else:
                    self.files_by_name.setdefault(fn.lower(), []).append(rel)
        self.wiki = sorted(r for r in self.md if r.startswith("wiki/"))

    def resolve(self, target, embed):
        t = target.strip()
        if not t:
            return None
        low = t.lower()
        if low.endswith(".md"):
            low = low[:-3]
        if "/" in low:
            if low in self.by_rel:
                return self.by_rel[low]
            hits = self.files_by_name.get(Path(t).name.lower())
            return hits[0] if hits else None
        if low in self.by_stem:
            return self.by_stem[low][0]
        hits = self.files_by_name.get(t.lower())
        if hits:
            return hits[0]
        return None


def check(vault_root, frontmatter_mode="sources", snapshot=None):
    v = Vault(vault_root)
    errors, warnings = [], []

    def err(kind, path, msg, line=None):
        errors.append({"check": kind, "file": path, "line": line, "msg": msg})

    def warn(kind, path, msg, line=None):
        warnings.append({"check": kind, "file": path, "line": line, "msg": msg})

    linted = [r for r in v.md if r.startswith("wiki/")] + \
             [r for r in ("index.md", "log.md") if r in v.md]
    inbound = {r: set() for r in v.wiki}

    # --- links -----------------------------------------------------------
    for rel in linted:
        text = v.md[rel]
        for ln, line in strip_code(text.split("\n")):
            for m in LINK_RE.finditer(line):
                embed, inner = m.group(1) == "!", m.group(2)
                target = parse_target(inner)
                if not target or re.match(r"^[a-z]+://", target, re.I):
                    continue        # [[#heading]] self-link, or a real URL
                hit = v.resolve(target, embed)
                if hit is None:
                    # log.md is append-only history and can't be fixed in
                    # place, so a dead link there is only a warning.
                    (warn if rel == "log.md" else err)(
                        "broken-link", rel, f"[[{target}]] resolves to nothing", ln)
                elif hit in inbound and hit != rel and rel not in ("index.md", "log.md"):
                    inbound[hit].add(rel)
            rest = LINK_RE.sub("", line)
            if "[[" in rest:
                err("split-link", rel, "'[[' never closed on this line "
                    "(a wikilink split across a line break)", ln)

    # --- orphans ---------------------------------------------------------
    for rel in v.wiki:
        if not inbound[rel]:
            err("orphan", rel, "no inbound link from any other page")

    # --- index -----------------------------------------------------------
    index = v.md.get("index.md")
    if index is None:
        err("index-missing", "index.md", "index.md not found")
    else:
        section, listed = None, {}
        for ln, line in strip_code(index.split("\n")):
            h = re.match(r"^##\s+(.+?)\s*$", line)
            if h:
                section = h.group(1)
                continue
            if not re.match(r"^\s*[-*]\s+\[\[", line):
                continue
            # An entry may group pages: "- [[A]] / [[B]] — summary". Every
            # link before the first dash separator is an entry.
            parts = re.split(r"\s[—–]\s|\s-\s", line, maxsplit=1)
            head, summary = parts[0], (parts[1] if len(parts) > 1 else "")
            for m in LINK_RE.finditer(head):
                target = parse_target(m.group(2))
                hit = v.resolve(target, False)
                if hit is None or not hit.startswith("wiki/"):
                    err("index-stale", "index.md",
                        f"entry [[{target}]] is not a wiki page", ln)
                    continue
                listed.setdefault(hit, []).append(ln)
                want = SECTION_FOR_DIR.get(hit.split("/")[1])
                if want and section and want.lower() not in section.lower():
                    err("index-section", "index.md",
                        f"[[{target}]] is under '{section}', belongs under '{want}'", ln)
            if len(summary.strip()) < 3:
                err("index-summary", "index.md", f"line has no summary: {head.strip()[:60]}", ln)
        for rel in v.wiki:
            if rel not in listed:
                err("index-missing", rel, "not listed in index.md")
            elif len(listed[rel]) > 1:
                err("index-duplicate", "index.md",
                    f"{rel} listed {len(listed[rel])}x (lines {listed[rel]})")

    # --- frontmatter + clipping reconciliation ---------------------------
    claimed = {}
    for rel in v.wiki:
        fm, malformed, _ = split_frontmatter(v.md[rel])
        if malformed:
            err("frontmatter", rel, "opening '---' with no closing '---'")
            continue
        is_source = rel.startswith("wiki/sources/")
        if fm is None:
            if is_source or frontmatter_mode == "all":
                err("frontmatter", rel, "no frontmatter")
            continue
        if not is_source:
            continue
        for k in SOURCE_KEYS:
            if not fm.get(k):
                err("frontmatter", rel, f"missing '{k}:'")
        d = fm.get("date", "")
        if d and not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            err("frontmatter", rel, f"date '{d}' is not YYYY-MM-DD")
        clip = fm.get("clipping")
        if clip:
            if not (v.root / clip).exists():
                err("clipping-missing", rel, f"clipping '{clip}' does not exist")
            claimed.setdefault(clip, []).append(rel)
    proc = v.root / "raw" / "clippings" / "processed"
    if proc.is_dir():
        for p in sorted(proc.iterdir()):
            if p.name.startswith(".") or p.is_dir():
                continue
            rel = p.relative_to(v.root).as_posix()
            owners = claimed.get(rel, [])
            if not owners:
                err("clipping-orphan", rel, "no source page's clipping: points here")
            elif len(owners) > 1:
                err("clipping-dup", rel, f"claimed by {owners}")
    inbox = v.root / "raw" / "clippings"
    if inbox.is_dir():
        for p in sorted(inbox.iterdir()):
            if p.is_file() and not p.name.startswith("."):
                warn("inbox", p.relative_to(v.root).as_posix(), "not yet ingested")

    # --- log reconciliation ---------------------------------------------
    log = v.md.get("log.md", "")
    logged = [norm(t) for t in
              re.findall(r"^## \[\d{4}-\d{2}-\d{2}\] ingest \| (.+?)\s*$", log, re.M)]
    for rel in sorted(r for r in v.wiki if r.startswith("wiki/sources/")):
        stem = Path(rel).stem
        cands = {norm(re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", stem))}
        h1 = re.search(r"^# (.+)$", v.md[rel], re.M)
        if h1:
            cands.add(norm(h1.group(1)))
        # A companion source (e.g. a PDF a post attached) is logged under
        # its parent post's entry, named by `via:`.
        fm, _, _ = split_frontmatter(v.md[rel])
        if fm and fm.get("via"):
            via = parse_target(fm["via"].strip("[]"))
            cands.add(norm(re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", via)))
        if not any(c and (c == l or c in l or l in c) for c in cands for l in logged):
            err("log-missing", rel, "no '## [date] ingest | Title' entry in log.md")

    # --- provenance (only text added since the snapshot) ----------------
    if snapshot:
        for item in provenance_problems(v, Path(snapshot)):
            err("provenance", item[0], item[2], item[1])

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "counts": {"wiki_pages": len(v.wiki), "errors": len(errors),
                       "warnings": len(warnings)}}


def provenance_problems(v, snap):
    """Blocks of claim text added since the snapshot that carry no tag."""
    out = []
    for rel in v.wiki:
        new = v.md[rel].split("\n")
        old_p = snap / rel
        old = old_p.read_text(encoding="utf-8", errors="replace").split("\n") \
            if old_p.exists() else []
        added = set()
        sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
        for op, _, _, j1, j2 in sm.get_opcodes():
            if op in ("replace", "insert"):
                added.update(range(j1, j2))
        if not added:
            continue
        _, _, body0 = split_frontmatter(v.md[rel])
        for start, end in claim_blocks(new, body0):
            if not any(i in added for i in range(start, end)):
                continue
            text = " ".join(new[start:end])
            if TAG_RE.search(text):
                continue
            words = re.sub(r"\[\[[^\]]*\]\]", "", text)
            if len(re.findall(r"[A-Za-z]{2,}", words)) < 4:
                continue
            out.append((rel, start + 1,
                        "added text has no [src]/[bg] tag: "
                        + new[start].strip()[:90]))
    return out


def claim_blocks(lines, body0):
    """Yield (start, end) line ranges of taggable units: each list item,
    each paragraph, each whole table. Headings, callouts, exempt sections
    and frontmatter are skipped."""
    section, i, n = "", body0, len(lines)
    in_fence = False
    while i < n:
        line = lines[i]
        s = line.strip()
        if FENCE_RE.match(line):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence or not s:
            i += 1
            continue
        h = re.match(r"^(#{1,6})\s+(.*)$", s)
        if h:
            if len(h.group(1)) <= 3:
                section = re.sub(r"[^a-z ]", "", h.group(2).lower()).strip()
            i += 1
            continue
        exempt = section in PROVENANCE_EXEMPT_SECTIONS
        if s.startswith(">"):                       # hub callouts
            while i < n and lines[i].strip().startswith(">"):
                i += 1
            continue
        if s.startswith("|"):                       # a table is one unit
            j = i
            while j < n and lines[j].strip().startswith("|"):
                j += 1
            # a caption paragraph right after the table may carry its tag
            k = j + 1 if j < n and not lines[j].strip() else j
            if not exempt:
                end = j
                if k < n and TAG_RE.search(lines[k]):
                    end = k + 1
                yield i, end
            i = j
            continue
        if re.match(r"^\s*([-*+]|\d+[.)])\s+", line):  # list item + continuation
            j = i + 1
            while j < n and lines[j].strip() and not re.match(
                    r"^\s*([-*+]|\d+[.)])\s+|^\s*#|^\s*\|", lines[j]) \
                    and lines[j].startswith((" ", "\t")):
                j += 1
            if not exempt:
                yield i, j
            i = j
            continue
        j = i + 1                                   # paragraph
        while j < n and lines[j].strip() and not re.match(
                r"^\s*([-*+]|\d+[.)])\s+|^\s*#|^\s*\||^\s*>", lines[j]):
            j += 1
        if not exempt:
            yield i, j
        i = j


def fmt(e):
    loc = f"{e['file']}:{e['line']}" if e["line"] else e["file"]
    return f"{e['check']:<16} {loc} — {e['msg']}"


# A pipeline ingest sub-agent is told to leave these to the merge agent
# (index.md, log.md) and the orchestrator (moving the clipping), and its
# new pages are linked up at merge time, so they'd be false alarms mid-ingest.
INGEST_DEFERRED = {"index-missing", "orphan", "log-missing", "clipping-missing"}


def hook(mode, vault, marker, stdin=sys.stdin):
    """Claude Code hook entry point; always exits 0, speaks in JSON."""
    vault, marker = Path(vault), Path(marker)
    ingest = os.environ.get("VP_HOOK_ROLE") == "ingest"
    if mode == "--mark":
        if not ingest:       # parallel sub-agents would race on one marker
            marker.touch()
        return 0
    try:
        payload = json.load(stdin)
    except ValueError:
        payload = {}
    if not ingest:
        if not marker.exists():
            return 0
        since = marker.stat().st_mtime
        watched = list((vault / "wiki").rglob("*.md")) + [vault / "index.md", vault / "log.md"]
        if not any(p.exists() and p.stat().st_mtime > since for p in watched):
            return 0
    snap = os.environ.get("VP_SNAPSHOT") if ingest else None
    r = check(vault, os.environ.get("VP_FRONTMATTER", "sources"),
              snap if snap and Path(snap).is_dir() else None)
    errors = [e for e in r["errors"] if not (ingest and e["check"] in INGEST_DEFERRED)]
    if not errors:
        return 0
    errs = [fmt(e) for e in errors]
    listing = "\n".join(errs[:40]) + (f"\n…and {len(errs) - 40} more" if len(errs) > 40 else "")
    if payload.get("stop_hook_active"):
        # Already sent back once this turn; don't loop. Tell the user instead.
        print(json.dumps({"systemMessage": f"Wiki lint still has {len(errs)} "
                          f"issue(s) after a fix pass:\n{listing}"}))
    elif ingest:
        print(json.dumps({"decision": "block", "reason":
                          f"The structural lint found {len(errs)} issue(s) you should "
                          "fix. Index, log, orphan and clipping-location checks are "
                          "already excluded (they're the merge agent's). If an issue is "
                          "on a page you didn't touch, a sibling agent may be mid-edit: "
                          "leave it and note it in your manifest.\n" + listing}))
    else:
        print(json.dumps({"decision": "block", "reason":
                          "The vault changed this turn and the structural lint found "
                          f"{len(errs)} issue(s). Fix them (CLAUDE.md conventions), "
                          f"or say why one should stay:\n{listing}"}))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vault")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--snapshot", help="pre-run copy of the vault; enables "
                    "the provenance check on text added since")
    ap.add_argument("--frontmatter", choices=("sources", "all"),
                    default=os.environ.get("VP_FRONTMATTER", "sources"))
    a = ap.parse_args()
    r = check(a.vault, a.frontmatter, a.snapshot)
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        for e in r["errors"]:
            print("ERROR " + fmt(e))
        for w in r["warnings"]:
            print(f"warn  {w['check']:<16} {w['file']} — {w['msg']}")
        c = r["counts"]
        print(f"{'CLEAN' if r['ok'] else 'FAIL'}: {c['wiki_pages']} wiki pages, "
              f"{c['errors']} errors, {c['warnings']} warnings")
    sys.exit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()
