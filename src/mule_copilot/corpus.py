"""Clause-level corpus over the two RBI sources, embedded into Chroma.

    DRAFT  Draft RBI (KYC) Amendment Directions, 2026 (11 Sep 2026), incl. the Annex III SOP
    KYC    RBI (Commercial Banks - KYC) Directions, 2025 (updated 1 Oct 2026): only the
           monitoring, record-keeping, FIU-IND reporting and money-mule paragraphs

Both are fetched as HTML from rbi.org.in (the rbidocs PDF host refuses scripted requests).
Every clause gets a stable ID that case notes cite, e.g. "SOP Annex III para 5 step 3".

    python -m mule_copilot.corpus                  # fetch (if missing), split, embed
    python -m mule_copilot.corpus "some question"  # top matches
"""

import json
import re
import sys
import urllib.request
from functools import cache

from bs4 import BeautifulSoup

from mule_copilot import config

SOURCES = {
    "draft": {
        "url": "https://www.rbi.org.in/scripts/bs_viewcontent.aspx?Id=5170",
        "file": "rbi_draft_kyc_amendment_2026.html",
        "name": "Draft RBI (KYC) Amendment Directions, 2026 (11 Sep 2026)",
        "status": "draft",
    },
    "kyc": {
        "url": "https://www.rbi.org.in/scripts/BS_ViewMasDirections.aspx?id=13141",
        "file": "rbi_kyc_directions_cb_2025.html",
        "name": "RBI (Commercial Banks - KYC) Directions, 2025 (updated 1 Oct 2026)",
        "status": "in force",
    },
}
KYC_PARAS = {   # paragraph -> short title (most of these paragraphs have no heading of their own)
    39: "On-going due diligence",
    40: "Transactions that need close monitoring",
    41: "Monitoring in line with risk category",
    50: "Maintenance and preservation of records",
    52: "Reporting to FIU-IND",
    53: "FIU-IND reporting formats",
    54: "Delay in reporting to FIU-IND",
    55: "Alerts for suspicious transactions",
    68: "Operation of Bank Accounts and Money Mules",
}
COLLECTION = "rbi_clauses"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "   # bge query instruction

SECTION = re.compile(r"^(\d+)\. ([A-Z][^.:]{2,80})$")
STEP = re.compile(r"^\[\[STEP (\d+)\]\] (.*)$")
DEFINITION = re.compile(r"^“([^”]+)”")
ITEM = re.compile(r"^\(([ivx]+)\) ")
PARA = re.compile(r"^(\d+)\. ")


def fetch():
    config.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    for src in SOURCES.values():
        path = config.DOCS_DIR / src["file"]
        if not path.exists():
            req = urllib.request.Request(src["url"], headers={"User-Agent": "Mozilla/5.0"})
            path.write_bytes(urllib.request.urlopen(req, timeout=60).read())


def html_lines(html):
    """Readable lines from an RBI page; SOP step tables become one '[[STEP n]] ...' line each."""
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("table", class_="td") or soup.body
    for tr in body.find_all("tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) == 3 and cells[0].get_text(strip=True).isdigit():
            n, action, timeline = (" ".join(c.get_text(" ", strip=True).split()) for c in cells)
            tr.replace_with(soup.new_string(f"\n[[STEP {n}]] {action} Timeline: {timeline}\n"))
    for el in body.find_all(["p", "li", "br", "tr", "div"]):
        el.insert_after("\n")
    lines = (" ".join(line.split()) for line in body.get_text().split("\n"))
    return [line for line in lines if line]


def _clause(src, cid, title, text):
    s = SOURCES[src]
    return {"id": cid, "title": title, "text": text, "source": s["name"], "status": s["status"], "url": s["url"]}


def split_draft(lines):
    clauses = []
    start = next(i for i, line in enumerate(lines) if line.startswith("“Annex III"))

    # Amendment text before the Annex: purpose, commencement, and the substituted mule paragraph.
    pre = lines[:start]
    purpose = next(line for line in pre if line.startswith("Reserve Bank has issued"))
    clauses.append(_clause("draft", "Draft Amendment 2026 para 1", "Purpose of the amendment", purpose))
    i3 = next(i for i, line in enumerate(pre) if line.startswith("3. Short Title"))
    i4 = next(i for i, line in enumerate(pre) if line.startswith("4. "))
    clauses.append(_clause("draft", "Draft Amendment 2026 para 3", "Short title and commencement", " ".join(pre[i3 + 1:i4])))
    i41 = next(i for i, line in enumerate(pre) if line.startswith("(1) Applicable paragraph"))
    i42 = next(i for i, line in enumerate(pre) if line.startswith("(2) In Annexures"))
    clauses.append(_clause("draft", "Draft Amendment 2026 para 4(1)",
                           "Substituted paragraph: Operation of Bank Accounts and Money Mules", " ".join(pre[i41 + 1:i42])))

    section, title, buffer = None, None, []

    def close_section():
        if section and buffer:
            clauses.append(_clause("draft", f"SOP Annex III para {section}", title, " ".join(buffer)))

    for line in lines[start + 1:]:
        if line.startswith('”') or line.startswith('".'):
            break
        if (m := SECTION.match(line)) and not line.startswith("[[STEP"):
            close_section()
            section, title, buffer = int(m.group(1)), m.group(2), []
        elif m := STEP.match(line):
            clauses.append(_clause("draft", f"SOP Annex III para {section} step {m.group(1)}", f"{title}, step {m.group(1)}", m.group(2)))
        elif section == 3 and (m := DEFINITION.match(line)):
            clauses.append(_clause("draft", f"SOP Annex III para 3 ({m.group(1)})", f"Definition: {m.group(1)}", line))
        elif section == 6 and (m := ITEM.match(line)):
            clauses.append(_clause("draft", f"SOP Annex III para 6({m.group(1)})", f"{title}, item ({m.group(1)})", line))
        elif line.startswith("Maximum duration"):
            clauses.append(_clause("draft", f"SOP Annex III para {section} (maximum duration)", "Maximum duration of a temporary debit hold", line))
        elif line != "Step Action Timeline":
            buffer.append(line)
    close_section()
    return clauses


def split_kyc(lines):
    paras, current, expected = {}, None, 1
    for line in lines:
        m = PARA.match(line)
        if m and int(m.group(1)) == expected:   # paragraph numbers run in order; ignore stray "N. "
            current, expected = expected, expected + 1
            paras[current] = [line]
        elif line.startswith("Chapter ") or re.match(r"^[A-Z]\. [A-Z]", line):
            current = None
        elif current:
            paras[current].append(line)

    clauses = []
    for n in sorted(KYC_PARAS.keys() & paras.keys()):
        clauses.append(_clause("kyc", f"KYC-CB-2025 para {n}", KYC_PARAS[n], " ".join(paras[n])))

    definitions = paras.get(5, [])
    i = next((k for k, line in enumerate(definitions) if line.startswith("(xviii)")), None)
    if i is not None:
        j = next(k for k in range(i + 1, len(definitions)) if definitions[k].startswith("(xix)"))
        clauses.append(_clause("kyc", "KYC-CB-2025 para 5(1)(xviii)", "Definition: Suspicious transaction", " ".join(definitions[i:j])))
    return clauses


def build_clauses():
    fetch()
    read = lambda key: (config.DOCS_DIR / SOURCES[key]["file"]).read_text(encoding="utf-8", errors="replace")
    return split_draft(html_lines(read("draft"))) + split_kyc(html_lines(read("kyc")))


@cache
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(config.EMBEDDING_MODEL)


@cache
def _collection():
    import chromadb
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR), settings=chromadb.Settings(anonymized_telemetry=False))
    return client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


@cache
def clauses_by_id():
    with open(config.CLAUSES, encoding="utf-8") as f:
        return {c["id"]: c for c in map(json.loads, f)}


def build():
    clauses = build_clauses()
    config.CLAUSES.parent.mkdir(parents=True, exist_ok=True)
    with open(config.CLAUSES, "w", encoding="utf-8") as f:
        for c in clauses:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    collection = _collection()
    if existing := collection.get()["ids"]:
        collection.delete(ids=existing)
    docs = [f"{c['title']}. {c['text']}" for c in clauses]
    collection.add(
        ids=[c["id"] for c in clauses],
        documents=docs,
        embeddings=_model().encode(docs, normalize_embeddings=True).tolist(),
        metadatas=[{"source": c["source"], "status": c["status"]} for c in clauses],
    )
    clauses_by_id.cache_clear()
    return clauses


def search(query, k=4):
    """Top-k clauses for a query, best first, each with a cosine similarity score."""
    embedding = _model().encode([QUERY_PREFIX + query], normalize_embeddings=True).tolist()
    hits = _collection().query(query_embeddings=embedding, n_results=k)
    by_id = clauses_by_id()
    return [dict(by_id[cid], score=round(1 - dist, 3)) for cid, dist in zip(hits["ids"][0], hits["distances"][0])]


def main():
    if len(sys.argv) > 1:
        for hit in search(" ".join(sys.argv[1:])):
            print(f"{hit['score']:.3f}  [{hit['id']}]  {hit['text'][:140]}")
        return
    clauses = build()
    print(f"{len(clauses)} clauses -> {config.CLAUSES} and Chroma collection '{COLLECTION}'")
    for c in clauses:
        print(f"  [{c['id']}] {c['title'][:70]}")


if __name__ == "__main__":
    main()
