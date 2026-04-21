# Prompt history

Exported Claude Code session transcripts from the build, per the hackathon brief's "please plan to export your prompt history" requirement.

Four substantive sessions, each in its own folder named after the session's topic. Chronological order (earliest first):

| Folder | When | Topic |
|---|---|---|
| `kickoff-and-intake-build/` | Apr 19 | Read the hackathon brief, plan the build, implement intake (chat → structured work order + Google Places autocomplete) |
| `vendor-discovery-build/` | Apr 19 | Fake-data generation + live Google Places pipeline + BBB enrichment + scoring + filters for subpart 2 |
| `subpart-3-vendor-auction-build/` | Apr 20 | The main engineering session — state machine, scheduler, coordinator + simulator LLM agents, persona pool, command-center UI |
| `distribution-readme-and-prompts/` | Apr 21 | Packaging the project for distribution — Makefile, README, `.env.example`, this prompt export |

## Two formats per session

Inside each folder:

- **`conversation.md`** — human-readable transcript. Just the user prompts and Claude's prose responses, with tool calls / tool results / thinking blocks / CLI command noise stripped out. Read this.
- **`conversation.jsonl`** — raw Claude Code session log. Every event in the session: tool calls, tool results, file snapshots, token accounting, model metadata. Open this if you want the full trace.

Both files cover the exact same session — the `.md` is a derived view of the `.jsonl`.

## Re-exporting

Claude Code stores session logs at:

```
~/.claude/projects/-Users-Lucasciordasna-Desktop-TaviHackathon/
```

To refresh, copy the new `.jsonl` into the appropriate folder as `conversation.jsonl`, then re-run the converter snippet below to regenerate `conversation.md`.

## Converter

Python, run from the repo root. Walks each subfolder, reads `conversation.jsonl`, and rewrites `conversation.md`.

```python
import json, re
from pathlib import Path

PROMPTS_DIR = Path("docs/prompts")
NOISE = re.compile(r'<(local-command-[a-z]+|command-[a-z]+|system-reminder)\b[^>]*>.*?</\1>', re.DOTALL)

def extract_text(content):
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "\n\n".join(
        b.get("text", "").strip()
        for b in content
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip()
    )

for folder in sorted(PROMPTS_DIR.iterdir()):
    jsonl = folder / "conversation.jsonl"
    if not jsonl.exists():
        continue
    convo, started, session_id = [], None, None
    for line in jsonl.open():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if session_id is None:
            session_id = rec.get("sessionId")
        if rec.get("isSidechain") or rec.get("type") not in ("user", "assistant"):
            continue
        raw = extract_text((rec.get("message") or {}).get("content", ""))
        text = NOISE.sub('', raw).strip() if rec["type"] == "user" else raw
        if not text:
            continue
        if started is None and rec["type"] == "user":
            started = rec.get("timestamp")
        convo.append(("User" if rec["type"] == "user" else "Claude", text))

    out = [f"# {folder.name}", ""]
    if session_id:
        out += [f"_Session ID: {session_id}_", ""]
    if started:
        out += [f"_Started: {started}_", ""]
    out += [f"_{len(convo)} messages (prose only)_", "", "---", ""]
    for role, text in convo:
        out += [f"## {role}", "", text, "", "---", ""]
    (folder / "conversation.md").write_text("\n".join(out))
```
