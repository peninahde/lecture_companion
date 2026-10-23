"""Optional export of a lecture's explanations to a Notion page.

Setup (once): create an internal integration at https://www.notion.so/my-integrations,
copy its token, then open the Notion page you want to export under and use
"... -> Connections -> <your integration>" so the integration can write there.
"""

from __future__ import annotations

import re

from notion_client import Client
from notion_client.helpers import collect_paginated_api

MAX_TEXT = 1900       # Notion limit is 2000 characters per rich-text object
BATCH = 90            # Notion limit is 100 blocks per request


def page_id_from(url_or_id: str) -> str:
    # The ID is the last 32 hex characters of the link's final path segment.
    last = url_or_id.strip().split("?")[0].split("#")[0].rstrip("/").split("/")[-1]
    candidate = last.replace("-", "")[-32:]
    if not re.fullmatch(r"[0-9a-fA-F]{32}", candidate):
        raise ValueError("Couldn't find a Notion page ID in that link.")
    return candidate.lower()


def _rich(text: str) -> list[dict]:
    """Convert **bold**, `code` and $math$ inside a line to Notion rich text."""
    out = []
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`|\$[^$]+\$)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            out.append({"type": "text", "text": {"content": part[2:-2][:MAX_TEXT]},
                        "annotations": {"bold": True}})
        elif part.startswith("`") and part.endswith("`"):
            out.append({"type": "text", "text": {"content": part[1:-1][:MAX_TEXT]},
                        "annotations": {"code": True}})
        elif part.startswith("$") and part.endswith("$") and len(part) > 2:
            out.append({"type": "equation", "equation": {"expression": part[1:-1]}})
        else:
            for i in range(0, len(part), MAX_TEXT):
                out.append({"type": "text", "text": {"content": part[i:i + MAX_TEXT]}})
    return out


def _block(kind: str, text: str) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": _rich(text)}}


def markdown_to_blocks(md: str) -> list[dict]:
    blocks, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        line = lines[i].rstrip()
        s = line.strip()
        if s.startswith("```"):
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append({"object": "block", "type": "code", "code": {
                "rich_text": [{"type": "text", "text": {"content": "\n".join(code)[:MAX_TEXT]}}],
                "language": "plain text"}})
        elif s.startswith("$$"):
            expr = s[2:]
            while not expr.endswith("$$") and i + 1 < len(lines):
                i += 1
                expr += "\n" + lines[i].strip()
            blocks.append({"object": "block", "type": "equation",
                           "equation": {"expression": expr.strip("$").strip()}})
        elif s.startswith("|"):
            blocks.append({"object": "block", "type": "code", "code": {   # tables as monospace
                "rich_text": [{"type": "text", "text": {"content": s}}], "language": "plain text"}})
        elif s.startswith("### "):
            blocks.append(_block("heading_3", s[4:]))
        elif s.startswith("## "):
            blocks.append(_block("heading_2", s[3:]))
        elif s.startswith("# "):
            blocks.append(_block("heading_1", s[2:]))
        elif re.match(r"^[-*] ", s):
            blocks.append(_block("bulleted_list_item", s[2:]))
        elif re.match(r"^\d+[.)] ", s):
            blocks.append(_block("numbered_list_item", re.sub(r"^\d+[.)] ", "", s)))
        elif s:
            blocks.append(_block("paragraph", s))
        i += 1
    return blocks


def list_pages(parent: str, token: str) -> list[tuple[str, str]]:
    """(title, page id) for the parent page itself, then each child page under it."""
    notion = Client(auth=token)
    pid = page_id_from(parent)
    props = notion.pages.retrieve(page_id=pid)["properties"].values()
    title = next(("".join(t["plain_text"] for t in p["title"])
                  for p in props if p["type"] == "title"), "") or "Untitled"
    children = collect_paginated_api(notion.blocks.children.list, block_id=pid)
    return [(title, pid)] + [(b["child_page"]["title"] or "Untitled", b["id"])
                             for b in children if b["type"] == "child_page"]


def export(markdown: str, title: str, parent: str, token: str) -> str:
    """Create a child page under `parent` and return its URL."""
    notion = Client(auth=token)
    blocks = markdown_to_blocks(markdown)
    page = notion.pages.create(
        parent={"page_id": page_id_from(parent)},
        properties={"title": {"title": [{"type": "text", "text": {"content": title[:200]}}]}},
        children=blocks[:BATCH],
    )
    for start in range(BATCH, len(blocks), BATCH):
        notion.blocks.children.append(block_id=page["id"], children=blocks[start:start + BATCH])
    return page.get("url", "")
