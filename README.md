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

- **[`docs/requirements.md`](docs/requirements.md)** — the spec (16 sections, 11 ADRs)
- **[`CLAUDE.md`](CLAUDE.md)** — contributor and agent guide: commands, TDD protocol, invariants

## Running with Docker

One image serves the API, the MCP server (`/mcp`) and the web UI (`/`, `/ingest`,
`/browse`) from a single container (spec §4.2).

```bash
cp config.yaml.example config.yaml        # then edit: model base_url, vaults, auth
export GT_API_KEY=...                     # secrets are env vars only (§11.4)
docker compose up --build
open http://localhost:8000/               # web UI;  /health is unauthenticated
```

- **`state:/var/lib/groundtruth`** — job records, source index, vault registry,
  optional LLM logs. A named volume, deliberately **outside** any vault repo (§5.1).
- **`vaults:/data`** — vault repos. Put each vault at `/data/<name>` and list it
  under `vaults:` in `config.yaml`, or adopt one at runtime via `POST /vaults`.
- Commits carry the dedicated `groundtruth` git identity, configured in the image.
- The container runs as a non-root user; no secret is baked into any layer.
- Data survives `docker compose restart` — everything lives in the named volumes.

## Running on Kubernetes

A Helm chart lives in [`charts/groundtruth`](charts/groundtruth). The image and the
chart are published to GitHub Container Registry on every `v*` tag:

```bash
helm install gt oci://ghcr.io/psenna/charts/groundtruth --version <x.y.z> \
  --set-string secret.data.GT_API_KEY=... \
  --set config.defaults.models.default.base_url=http://your-llm/v1
```

- One `Deployment` (replica count fixed at 1 — groundtruth is single-writer),
  `Recreate` strategy, non-root with all capabilities dropped.
- Two `ReadWriteOnce` PVCs: `state` (`/var/lib/groundtruth`) and `vaults`
  (`/data`) — the state dir is deliberately outside every vault repo (§5.1).
- `config.yaml` is a `ConfigMap` rendered from `.Values.config`; secrets are
  environment variables only (`.Values.secret.existingSecret` or
  `.Values.secret.data`), referenced by name from the config (§11.4).
- See [`charts/groundtruth/values.yaml`](charts/groundtruth/values.yaml) for all
  options (ingress, resources, storage classes, probes).

## Development

```bash
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run mypy src/
```

## License

MIT
