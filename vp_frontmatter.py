#!/usr/bin/env python3
"""Derived frontmatter for the vault's non-source wiki pages. Zero LLM.

Every concept, company and person page carries these keys, all computed
from the page itself, so they can never drift from its text:

  type          concept | company | person            (from the folder)
  ticker        e.g. ADBE, 9618.HK                    (companies, from the name)
  hub           true | false                          (the "> **HUB**" callout)
  sources       "[[source page]]" list                (the ## Source list)
  first_source  YYYY-MM-DD, earliest of those sources (their `date:`)
  last_source   YYYY-MM-DD, latest

Any other frontmatter key on a page is kept as is. Source pages are left
alone, because their frontmatter is hand-written (date/author/clipping).

  vp_frontmatter.py VAULT              # report pages out of sync; exit 1 if any
  vp_frontmatter.py VAULT --show N     # print N proposed frontmatter blocks
  vp_frontmatter.py VAULT --apply      # write them
"""
import argparse
import re
import sys
from pathlib import Path

TYPE_FOR_DIR = {"concepts": "concept", "companies": "company", "people": "person"}
DERIVED = ("type", "ticker", "hub", "sources", "first_source", "last_source")
LINK_RE = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
TICKER_RE = re.compile(r"^(?=.*[A-Z])[A-Z0-9]{1,6}(\.[A-Z]{1,2})?$|^\d{3,4}\.HK$")


def ticker_for(stem):
    m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", stem)
    if m and TICKER_RE.match(m.group(2).strip()):
        return m.group(2).strip()
    lead = stem.split(" ", 1)[0]
    # ETFs are named "QQQ (Nasdaq-100 ETF)": the ticker leads, parens describe.
    if m and TICKER_RE.match(lead) and lead.isupper():
        return lead
    if not m and TICKER_RE.match(stem) and stem.isupper():
        return stem                          # e.g. "MSCI"
    return None


def split_fm(text):
    """(list of (key, lines) blocks, body). Blocks keep raw lines verbatim."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 3)
    if end < 0:
        end = text.find("\n---", 3)
        if end < 0 or text[end + 4:].strip():
            return None, text
    raw, body = text[4:end], text[end + 5:]
    blocks = []
    for line in raw.split("\n"):
        m = re.match(r"^([A-Za-z0-9_-]+):", line)
        if m or not blocks:
            blocks.append([m.group(1) if m else "", [line]])
        else:
            blocks[-1][1].append(line)
    return blocks, body


class Deriver:
    def __init__(self, vault):
        self.vault = Path(vault)
        self.source_dates = {}
        for p in (self.vault / "wiki" / "sources").glob("*.md"):
            blocks, _ = split_fm(p.read_text(encoding="utf-8"))
            d = None
            for k, lines in blocks or []:
                if k == "date":
                    d = lines[0].split(":", 1)[1].strip().strip('"\'')
            self.source_dates[p.stem.lower()] = (p.stem, d)

    def pages(self):
        for d in TYPE_FOR_DIR:
            yield from sorted((self.vault / "wiki" / d).glob("*.md"))

    def derive(self, path, text):
        kind = TYPE_FOR_DIR[path.parent.name]
        _, body = split_fm(text)
        sources, in_src = [], False
        for line in body.split("\n"):
            h = re.match(r"^#{1,6}\s+(.*)$", line)
            if h:
                in_src = h.group(1).strip().lower() in ("source", "sources")
                continue
            if in_src:
                for m in LINK_RE.finditer(line):
                    t = re.split(r"\\?\|", m.group(1), maxsplit=1)[0].split("#")[0].strip()
                    hit = self.source_dates.get(t.lower())
                    if hit and hit[0] not in [s for s, _ in sources]:
                        sources.append(hit)
        dates = sorted(d for _, d in sources if d)
        out = {"type": kind}
        if kind == "company":
            t = ticker_for(path.stem)
            if t:
                out["ticker"] = t
        out["hub"] = "true" if re.search(r"^>\s*\*\*HUB", body, re.M) else "false"
        out["sources"] = [s for s, _ in sources]
        if dates:
            out["first_source"], out["last_source"] = dates[0], dates[-1]
        return out

    @staticmethod
    def render(fields):
        lines = []
        for k in DERIVED:
            if k not in fields:
                continue
            v = fields[k]
            if k == "sources":
                lines.append("sources:" if v else "sources: []")
                lines += [f'  - "[[{s}]]"' for s in v]
            else:
                lines.append(f"{k}: {v}")
        return lines

    def proposed(self, path):
        text = path.read_text(encoding="utf-8")
        blocks, body = split_fm(text)
        kept = [lines for k, lines in (blocks or []) if k not in DERIVED]
        fm = [l for lines in kept for l in lines if l.strip()] + \
            self.render(self.derive(path, text))
        new = "---\n" + "\n".join(fm) + "\n---\n" + body
        if blocks is None:
            assert new.endswith(text), path        # body must be untouched
        return text, new


def out_of_sync(vault):
    """[(relpath, reason)] for pages whose derived frontmatter is missing
    or stale. vp_lint calls this."""
    dv = Deriver(vault)
    bad = []
    for p in dv.pages():
        old, new = dv.proposed(p)
        if old != new:
            rel = p.relative_to(dv.vault).as_posix()
            bad.append((rel, "no frontmatter" if not old.startswith("---\n")
                        else "derived frontmatter is stale"))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vault")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show", type=int, default=0)
    a = ap.parse_args()
    dv = Deriver(a.vault)
    changes = []
    for p in dv.pages():
        old, new = dv.proposed(p)
        if old != new:
            changes.append((p, old, new))
    for p, old, new in changes[:a.show]:
        print(f"== {p.relative_to(dv.vault)}")
        print(new.split("\n---\n", 1)[0] + "\n---")
    if a.apply:
        for p, old, new in changes:
            assert p.read_text(encoding="utf-8") == old, f"{p} changed under us"
            p.write_text(new, encoding="utf-8")
        print(f"wrote derived frontmatter on {len(changes)} page(s)")
        return 0
    print(f"{len(changes)} page(s) out of sync")
    return 1 if changes else 0


if __name__ == "__main__":
    sys.exit(main())
