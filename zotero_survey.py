#!/usr/bin/env python3
"""Zotero Collection Survey — browse collections and get AI-generated thematic surveys."""

import sqlite3
import sys
import json
import re
import textwrap
import urllib.request
import urllib.error
from pathlib import Path

ZOTERO_DB     = Path.home() / "Zotero" / "zotero.sqlite"
ZOTERO_DB_BAK = Path.home() / "Zotero" / "zotero.sqlite.bak"
CONFIG_PATH   = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "ollama": {
        "host":                "http://localhost:11434",
        "categorization_model": "gpt-oss",
        "survey_model":         "gpt-oss",
    },
    "max_papers": 300,
}


# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
        print(dim(f"  Created default config at {CONFIG_PATH}"))
    with CONFIG_PATH.open() as f:
        cfg = json.load(f)
    # Merge missing keys from defaults so older configs still work
    for section, values in DEFAULT_CONFIG.items():
        if section not in cfg:
            cfg[section] = values
        elif isinstance(values, dict):
            for k, v in values.items():
                cfg[section].setdefault(k, v)
    return cfg


# ── Terminal helpers ──────────────────────────────────────────────────────────

def bold(s):   return f"\033[1m{s}\033[0m"
def dim(s):    return f"\033[2m{s}\033[0m"
def cyan(s):   return f"\033[96m{s}\033[0m"
def green(s):  return f"\033[92m{s}\033[0m"
def yellow(s): return f"\033[93m{s}\033[0m"
def hr(char="─", width=65): return dim(char * width)

def wrap(text, width=65, indent=0):
    prefix = " " * indent
    return "\n".join(
        textwrap.fill(para, width=width, initial_indent=prefix, subsequent_indent=prefix)
        for para in text.split("\n") if para.strip()
    )


# ── Ollama client (no extra dependencies) ────────────────────────────────────

class OllamaClient:
    def __init__(self, host):
        self.host = host.rstrip("/")

    def _post(self, endpoint, payload):
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            f"{self.host}{endpoint}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            print(f"\nError: cannot reach Ollama at {self.host} — {e.reason}")
            print("Make sure Ollama is running:  ollama serve")
            sys.exit(1)

    def chat(self, model, messages):
        result = self._post("/api/chat", {"model": model, "messages": messages, "stream": False})
        return result["message"]["content"]

    def chat_stream(self, model, messages):
        """Yield text chunks from a streaming chat response."""
        data = json.dumps({"model": model, "messages": messages, "stream": True}).encode()
        req  = urllib.request.Request(
            f"{self.host}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                for raw_line in resp:
                    line = raw_line.decode().strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if chunk.get("done"):
                        break
        except urllib.error.URLError as e:
            print(f"\nError: cannot reach Ollama at {self.host} — {e.reason}")
            print("Make sure Ollama is running:  ollama serve")
            sys.exit(1)


# ── Database helpers ──────────────────────────────────────────────────────────

def _try_open_db(db_path, bak_path):
    for path in [db_path, bak_path]:
        if path.exists():
            try:
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                conn.execute("SELECT 1 FROM collections LIMIT 1")
                note = dim("  (using backup — main db locked)") if path == bak_path else ""
                return conn, note
            except sqlite3.OperationalError:
                continue
    return None, None


def open_db():
    conn, note = _try_open_db(ZOTERO_DB, ZOTERO_DB_BAK)
    if conn:
        return conn, note

    print(dim(f"  Zotero database not found at default location ({ZOTERO_DB.parent})."))
    while True:
        try:
            raw = input("  Enter your Zotero data folder path: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            sys.exit(0)
        folder = Path(raw).expanduser().resolve()
        db  = folder / "zotero.sqlite"
        bak = folder / "zotero.sqlite.bak"
        conn, note = _try_open_db(db, bak)
        if conn:
            return conn, note
        print(dim(f"  No readable Zotero database found in '{folder}'. Try again."))


def get_collections(conn):
    return conn.execute(
        "SELECT collectionID, collectionName, parentCollectionID "
        "FROM collections ORDER BY collectionName"
    ).fetchall()


def get_items(conn, collection_id):
    rows = conn.execute("""
        SELECT
            i.itemID,
            it.typeName                                                       AS itemType,
            (SELECT idv.value FROM itemData d
             JOIN itemDataValues idv ON d.valueID = idv.valueID
             JOIN fields f          ON d.fieldID  = f.fieldID
             WHERE d.itemID = i.itemID AND f.fieldName = 'title')            AS title,
            (SELECT idv.value FROM itemData d
             JOIN itemDataValues idv ON d.valueID = idv.valueID
             JOIN fields f          ON d.fieldID  = f.fieldID
             WHERE d.itemID = i.itemID AND f.fieldName = 'abstractNote')     AS abstract,
            (SELECT idv.value FROM itemData d
             JOIN itemDataValues idv ON d.valueID = idv.valueID
             JOIN fields f          ON d.fieldID  = f.fieldID
             WHERE d.itemID = i.itemID AND f.fieldName = 'date')             AS date,
            (SELECT idv.value FROM itemData d
             JOIN itemDataValues idv ON d.valueID = idv.valueID
             JOIN fields f          ON d.fieldID  = f.fieldID
             WHERE d.itemID = i.itemID AND f.fieldName = 'publicationTitle') AS publication
        FROM items i
        JOIN itemTypes       it ON i.itemTypeID    = it.itemTypeID
        JOIN collectionItems ci ON i.itemID        = ci.itemID
        WHERE ci.collectionID = ?
          AND it.typeName NOT IN ('attachment', 'note', 'annotation')
        ORDER BY date DESC NULLS LAST
    """, (collection_id,)).fetchall()

    items = []
    for row in rows:
        if not row["title"]:
            continue
        authors = conn.execute("""
            SELECT c.firstName, c.lastName
            FROM itemCreators ic
            JOIN creators     c  ON ic.creatorID     = c.creatorID
            JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID
            WHERE ic.itemID = ? AND ct.creatorType = 'author'
            ORDER BY ic.orderIndex
        """, (row["itemID"],)).fetchall()
        items.append({
            "id":          row["itemID"],
            "type":        row["itemType"],
            "title":       row["title"],
            "abstract":    row["abstract"] or "",
            "date":        (row["date"] or "")[:4],
            "publication": row["publication"] or "",
            "authors":     [(a["firstName"] + " " + a["lastName"]).strip() for a in authors],
        })
    return items


# ── AI helpers ────────────────────────────────────────────────────────────────

def _paper_line(item, max_abstract=250):
    authors = ", ".join(item["authors"][:3])
    if len(item["authors"]) > 3:
        authors += " et al."
    abstract = item["abstract"][:max_abstract].rstrip()
    if len(item["abstract"]) > max_abstract:
        abstract += "…"
    year = item["date"] or "n.d."
    return f'"{item["title"]}" — {authors} ({year})\n  {abstract}'


def categorize_papers(client, model, items, max_papers):
    sample = items[:max_papers]
    papers_text = "\n\n".join(
        f"{i+1}. {_paper_line(p)}" for i, p in enumerate(sample)
    )
    note = f"(showing {len(sample)} of {len(items)})" if len(items) > len(sample) else ""

    prompt = f"""I have a collection of {len(sample)} academic papers {note}.
Analyze their titles and abstracts, then group them into 5–8 meaningful thematic categories.

Return ONLY valid JSON with this exact structure — no prose, no markdown fences:
{{
  "categories": [
    {{
      "name": "Short Category Name",
      "description": "One-sentence description of what unites these papers.",
      "paper_indices": [1, 3, 7, ...]
    }}
  ]
}}

Every paper must appear in at least one category. Paper indices are 1-based.

Papers:
{papers_text}"""

    raw = client.chat(model, [{"role": "user", "content": prompt}])
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in model response. Raw output:\n{raw[:500]}")
    return json.loads(match.group())


def generate_survey(client, model, category_name, description, papers):
    details = "\n\n".join(
        f'• "{p["title"]}" ({", ".join(p["authors"][:2]) or "Unknown"}'
        f'{" et al." if len(p["authors"]) > 2 else ""}, {p["date"] or "n.d."})\n'
        f'  {p["abstract"][:400]}{"…" if len(p["abstract"]) > 400 else ""}'
        for p in papers
    )
    prompt = f"""Write a concise academic mini-survey (3–4 paragraphs, ~300 words) covering the papers below.
Topic: "{category_name}" — {description}

Guidelines:
• Open with the topic's significance and scope.
• Summarize key contributions, methods, and findings, citing papers as (Author, year).
• Note any recurring themes, debates, or open questions.
• Close with the overall state of knowledge.
• Cite only the papers listed; do not invent references.

Papers:
{details}"""

    print()
    for chunk in client.chat_stream(model, [{"role": "user", "content": prompt}]):
        print(chunk, end="", flush=True)
    print()


# ── UI helpers ────────────────────────────────────────────────────────────────

def display_collections(collections):
    children = {}
    for row in collections:
        parent = row["parentCollectionID"]
        children.setdefault(parent, []).append((row["collectionID"], row["collectionName"]))

    numbered = []

    def walk(parent_id, indent=0):
        for cid, name in sorted(children.get(parent_id, []), key=lambda x: x[1]):
            prefix    = "  " * indent
            connector = "└─ " if indent > 0 else ""
            numbered.append((cid, name))
            n = len(numbered)
            label = bold(name) if indent == 0 else name
            print(f"  {cyan(str(n).rjust(3))}  {prefix}{connector}{label}")
            walk(cid, indent + 1)

    walk(None)
    return numbered


def prompt_int(prompt_text, lo, hi):
    while True:
        try:
            val = int(input(f"\n{prompt_text} ").strip())
            if lo <= val <= hi:
                return val
            print(dim(f"  Please enter a number between {lo} and {hi}."))
        except ValueError:
            print(dim("  Please enter a valid number."))
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            sys.exit(0)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print()
    print(bold(cyan("  ╔══════════════════════════════════════════════════════╗")))
    print(bold(cyan("  ║          ZOTERO COLLECTION SURVEY                   ║")))
    print(bold(cyan("  ╚══════════════════════════════════════════════════════╝")))
    print()

    cfg        = load_config()
    ollama_cfg = cfg["ollama"]
    max_papers = cfg["max_papers"]
    cat_model  = ollama_cfg["categorization_model"]
    sur_model  = ollama_cfg["survey_model"]
    client     = OllamaClient(ollama_cfg["host"])

    print(dim(f"  host: {ollama_cfg['host']}  "
              f"| categorize: {cat_model}  | survey: {sur_model}"))
    print()

    conn, db_note = open_db()
    if db_note:
        print(db_note)

    collections = get_collections(conn)
    if not collections:
        print("No collections found.")
        sys.exit(0)

    print(f"  {bold('Your collections')}  {dim(f'({len(collections)} total)')}\n")
    print(hr())
    numbered = display_collections(collections)
    print(hr())

    choice = prompt_int(f"Select a collection [1–{len(numbered)}]:", 1, len(numbered))
    sel_id, sel_name = numbered[choice - 1]

    print(f"\n  Loading {bold(sel_name)}…", end=" ", flush=True)
    items = get_items(conn, sel_id)
    conn.close()

    if not items:
        print(f"\n  No citable items found in '{sel_name}'.")
        sys.exit(0)

    print(f"{green('done')}  ({len(items)} papers)")

    # ── Categorize ────────────────────────────────────────────────────────────
    print(f"\n  {dim(f'Asking {cat_model} to identify thematic categories…')}", flush=True)

    try:
        result = categorize_papers(client, cat_model, items, max_papers)
    except Exception as e:
        print(f"\nError during categorization: {e}")
        sys.exit(1)

    categories = result.get("categories", [])
    if not categories:
        print("Could not extract categories from model response.")
        sys.exit(1)

    sample_size = min(len(items), max_papers)
    for cat in categories:
        cat["items"] = [
            items[i - 1]
            for i in cat.get("paper_indices", [])
            if 1 <= i <= sample_size
        ]

    # ── Display categories ────────────────────────────────────────────────────
    print()
    print(hr())
    print(f"  {bold('Categories in')} {bold(yellow(sel_name))}\n")
    for i, cat in enumerate(categories, 1):
        count = len(cat["items"])
        print(f"  {cyan(str(i).rjust(2))}.  {bold(cat['name'])}  {dim(f'({count} papers)')}")
        print(dim(f"       {cat['description']}"))
    print(hr())

    choice2 = prompt_int(f"Select a category to survey [1–{len(categories)}]:", 1, len(categories))
    sel_cat = categories[choice2 - 1]

    print()
    print(hr())
    print(f"  {bold('SURVEY:')} {bold(yellow(sel_cat['name']))}")
    print(hr())
    print(dim(f"  {sel_cat['description']}"))
    print(dim(f"  {len(sel_cat['items'])} papers  |  model: {sur_model}"))
    print()

    generate_survey(client, sur_model, sel_cat["name"], sel_cat["description"], sel_cat["items"])

    # ── Paper list ────────────────────────────────────────────────────────────
    print()
    print(hr())
    print(f"  {bold('Papers in this category:')}\n")
    for p in sel_cat["items"]:
        authors = ", ".join(p["authors"][:2])
        if len(p["authors"]) > 2:
            authors += " et al."
        year = p["date"] or "n.d."
        print(f"  • {wrap(p['title'], width=60, indent=6).lstrip()}")
        print(dim(f"      {authors}, {year}"))
    print()


if __name__ == "__main__":
    main()
