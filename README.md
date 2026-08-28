# groundtruth

Ingest unstructured text into an Obsidian-compatible vault, and answer questions **only**
from what that vault actually contains — with citations, or an explicit refusal.

The name states the guarantee. *Ground truth* is the verified reality you check a model's
output against, not the model's output itself. `groundtruth` never fills a gap from model
knowledge: if the answer isn't in the vault, it says so.

## How it works

Two pipelines over a file-based, git-versioned store:

- **Ingestion** — raw text in → tagged, distilled, cross-linked Markdown notes out. One
  ingest is exactly one commit, all-or-nothing, so `git revert` is a complete retraction.
- **Recovery** — a question in → a Markdown answer with `[[citations]]` pointing at the
  notes it came from, or a refusal.

The store is a plain Obsidian vault: Markdown files, tags, and `[[wikilinks]]`, versioned
with git. Retrieval is an LLM agent running `ls`/`grep`/`read` over those files — no
embeddings, no separate index that can drift from the notes.

Surfaces: REST API, MCP server (so external agents can query and contribute), and a
read-only web UI. Editing happens in Obsidian.

## Status

Pre-implementation. The specification is complete and normative:

- **[`docs/requirements.md`](docs/requirements.md)** — the spec (16 sections, 11 ADRs)
- **[`CLAUDE.md`](CLAUDE.md)** — contributor and agent guide: commands, TDD protocol, invariants

Work is tracked as GitHub issues across milestones M1–M9. Start with M1.

## Development

```bash
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run mypy src/
```

## License

MIT
