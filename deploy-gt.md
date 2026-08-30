# Deploying groundtruth on k3s (NodePort + local Ollama)

A single-node knowledge base for your projects — `groundtruth`, `dependaproxy`,
`git-proxy`, `ai-sandbox` — plus development and technology best practices. Agents
query it over MCP; you (and agents) feed it by ingesting docs.

- **One vault**, `knowledge`, a git repo on a PVC.
- **One NodePort** exposes the REST API, the web UI and the MCP endpoint (`/mcp`).
- **One model**: your Ollama `gemma4:31b` at `192.168.5.150:11434`, 100k context
  (set server-side — groundtruth does not send `num_ctx`).

> **Cut `v0.0.2` first.** `service.nodePort` and `config.llm_timeout_s` (and the
> Dockerfile fix that makes the image build at all) landed after `v0.0.1`, and
> the `v0.0.1` release run failed at the image step — so **nothing is published
> yet**. Tag `v0.0.2` on `main` (`git tag v0.0.2 <main-sha> && git push origin
> v0.0.2`); `release.yml` then publishes both packages to GHCR:
>
> | Package | Reference |
> |---|---|
> | image | `ghcr.io/psenna/groundtruth:0.0.2` (also `:0.0`, `:latest`) |
> | chart | `oci://ghcr.io/psenna/charts/groundtruth` version `0.0.2` |
>
> **Make both packages public** (one-time): GitHub → your profile → *Packages* →
> `groundtruth` and `charts/groundtruth` → *Package settings* → *Change
> visibility → Public*, and *Connect repository*. Then neither `helm` nor the
> k3s nodes need registry credentials. (Keeping them private is covered in §2.)
>
> Before the tag exists you can still install from a checkout of `main`:
> `helm upgrade --install gt ./charts/groundtruth -f values-knowledge.yaml` with
> `image.tag` set to a tag that does exist.

---

## 1. `values-knowledge.yaml`

```yaml
# groundtruth on k3s — knowledge base for psenna's projects + best practices.

replicaCount: 1                       # single-writer by design; never raise

image:
  repository: ghcr.io/psenna/groundtruth
  tag: "0.0.2"                        # must be a published tag; bump on each release

# Only if the image package is PRIVATE (see §2). Public → delete this block.
# imagePullSecrets:
#   - name: ghcr

service:
  type: NodePort
  port: 8000
  nodePort: 30800                     # http://<any-node-ip>:30800  → API + web + /mcp

# One RWO volume for the vault git repo, one for the state dir (job records,
# source index, registry). Set storageClass to your k3s provisioner
# ("local-path" is the k3s default) or leave "" to use the cluster default.
persistence:
  vaults:
    size: 5Gi
    storageClass: "local-path"
    retainOnDelete: true             # survives `helm uninstall`
  state:
    size: 1Gi
    storageClass: "local-path"
    retainOnDelete: true

# gemma is a big model on local hardware — give calls room and match the
# agent-loop budget to it, or every ingest/query trips the wall clock.
resources:
  requests: {cpu: 100m, memory: 512Mi}
  limits: {memory: 2Gi}

# Slow first token on a cold model — don't let the probe kill the pod mid-call.
livenessProbe:
  httpGet: {path: /health, port: http}
  initialDelaySeconds: 15
  periodSeconds: 30
  timeoutSeconds: 5
  failureThreshold: 6

config:
  server:
    bind: 0.0.0.0
    port: 8000
    auth: none                       # trusted LAN. See "Locking it down" below.
    mcp_endpoint: /mcp

  state_dir: /var/lib/groundtruth
  job_retention_days: 30
  llm_logging: false

  # HTTP timeout per LLM call. gemma4:31b prefilling ~100k tokens can take
  # minutes; a timed-out call is retried, so a job fails after ~3x this.
  llm_timeout_s: 600

  # Registered on startup. repo_root is the git repo; the Obsidian vault dir is
  # <repo_root>/<name>, i.e. /data/knowledge/knowledge. Scaffold it first (§3).
  vaults:
    knowledge: /data/knowledge

  defaults:
    raw_archive: true                # keep the source text alongside the notes
    auto_push: false                 # no git remote; the PVC is the source of truth
    allow_schema_writes: true        # lets the MCP update_schema tool refine schema.md

    # One model for every role. All four keys must be set — otherwise the
    # built-in defaults leave tag/reduce/answer pointing at models you don't have.
    models:
      default:
        base_url: http://192.168.5.150:11434/v1
        model: gemma4:31b
        api_key_env: GT_API_KEY      # unset → no auth header (Ollama ignores it)
      tag:    {model: gemma4:31b}
      reduce: {model: gemma4:31b}
      answer: {model: gemma4:31b}

    limits:
      max_notes_per_ingest: 10
      max_note_bytes: 65536
      max_tool_calls: 40
      max_wall_clock_s: 900          # overall agent-loop budget — MUST be well
                                     # above llm_timeout_s for a slow model
      grep_max_matches: 50
      grep_max_bytes: 65536
      read_max_bytes: 32768
      vocab_max_bytes: 8192
```

No `secret:` block — Ollama needs no key, and `auth: none` needs no token.

---

## 2. Deploy

Helm 3.8+ speaks OCI natively — the chart is an OCI artifact at
`oci://ghcr.io/psenna/charts/groundtruth`.

```sh
helm upgrade --install gt oci://ghcr.io/psenna/charts/groundtruth \
  --version 0.0.2 \
  -n groundtruth --create-namespace \
  -f values-knowledge.yaml

kubectl -n groundtruth rollout status deploy/gt-groundtruth
kubectl -n groundtruth get svc gt-groundtruth        # confirm nodePort 30800
```

`--version` is required for an OCI install — there is no `index.yaml` to resolve
a floating version. Inspect before installing with
`helm show values oci://ghcr.io/psenna/charts/groundtruth --version 0.0.2`.

### If the packages are private

Two separate credentials — the chart is pulled by **your `helm` client**, the
image by **the k3s nodes**.

```sh
# helm client → chart
echo "$GITHUB_PAT" | helm registry login ghcr.io -u <github-user> --password-stdin

# k3s nodes → image
kubectl -n groundtruth create secret docker-registry ghcr \
  --docker-server=ghcr.io --docker-username=<github-user> --docker-password="$GITHUB_PAT"
# then uncomment imagePullSecrets in values-knowledge.yaml
```

`$GITHUB_PAT` needs `read:packages`. Making both packages public (see the note at
the top) removes both of these steps.

Set a base URL for the rest of this doc (any node IP):

```sh
export GT=http://192.168.5.150:30800     # or another k3s node
curl -s $GT/health                       # {"status":"ok"}
```

If Ollama runs on a **different** host than your k3s nodes, that's fine —
`192.168.5.150:11434` just has to be reachable from the pod network. Test from
inside the pod:

```sh
kubectl -n groundtruth exec deploy/gt-groundtruth -- \
  python -c "import urllib.request;print(urllib.request.urlopen('http://192.168.5.150:11434/v1/models').read()[:200])"
```

---

## 3. Create the vault

The chart registers `knowledge → /data/knowledge` on startup but does **not**
create it. Scaffold it once (git init + starter layout + first commit):

```sh
curl -s -X POST $GT/vaults \
  -H 'content-type: application/json' \
  -d '{"name":"knowledge","repo_root":"/data/knowledge","init":true}'
# → 201 {"name":"knowledge","repo_root":"/data/knowledge","vault_dir":"/data/knowledge/knowledge"}
```

`GET $GT/vaults` should now list it. (If the pod restarts, the registry entry is
already persisted in the state PVC and the `init:true` call would 422 — that's
expected; it's a one-time step.)

---

## 4. Curate the schema

`schema.md` is the map both pipelines read first: the folder allowlist (the LLM
may only create notes in folders you list) and prescriptive tag guidance. Replace
the starter with one built for this vault:

```sh
kubectl -n groundtruth exec -i deploy/gt-groundtruth -- sh -s <<'EOF'
set -e
cd /data/knowledge
cat > knowledge/schema.md <<'SCHEMA'
# Schema

Knowledge base for psenna's projects and how we build them. Every note cites the
source it was distilled from; if a fact isn't in here, groundtruth says so.

## Folders

<!-- The LLM may only create notes in these folders. -->
- projects/ — one note per project: what it is, what problem it solves, its
  moving parts, how the pieces fit. `groundtruth`, `dependaproxy`, `git-proxy`,
  `ai-sandbox`.
- architecture/ — cross-cutting technical decisions and "how X actually works"
  deep-dives: ADRs, security boundaries, protocol/format choices, trade-offs and
  the reasoning behind them.
- practices/ — how we run the development process: TDD, one-issue-per-PR, the
  plan→implement→validate loop, commit and PR conventions, review expectations,
  when to stop and ask.
- stack/ — technology best practices we hold ourselves to: Go, Python, Helm,
  Docker, Kubernetes, CI pipeline patterns, dependency and supply-chain hygiene.
- runbooks/ — operational how-tos: cutting a release, deploying, reading CI logs,
  reproducing a CI failure locally.

## Tags

<!-- Lowercase, dash-separated. This is guidance, not an inventory — the tags
     actually in use are derived from the notes themselves. -->
- Tag every note with the project(s) it concerns: `groundtruth`, `dependaproxy`,
  `git-proxy`, `ai-sandbox`. Use `cross-project` for notes that span all of them.
- Use `adr` for a recorded decision, `security` for a trust-boundary or
  threat-model note, `gotcha` for a sharp edge worth warning about.
- Topic tags: `ci`, `helm`, `docker`, `kubernetes`, `testing`, `git`, `release`,
  `supply-chain`, `mcp`.
- Language tags: `go`, `python`.
- Prefer `process` over `workflow`; prefer `dependency` over `library`.
SCHEMA
git add knowledge/schema.md
git -c user.email=you@localhost -c user.name=you commit -q -m "schema(knowledge): curated folders + tags"
git log --oneline -2
EOF
```

Check it: `curl -s $GT/schema/knowledge`.

Working tree must stay **clean** for ingestion — the commit above handles that.
From now on, agents can refine the schema through the MCP `update_schema` tool
(enabled by `allow_schema_writes: true`).

---

## 5. Seed the content

groundtruth builds notes from text you ingest — it distills, tags and cross-links
each submission and commits it. Feed it a focused doc at a time (smaller inputs =
faster on a big local model).

`POST /ingest` takes `{vault, text, source_label}`. `?wait=true` blocks up to
120s then hands back a job id.

```sh
ingest() {  # ingest <source_label> <file>
  jq -Rs --arg v knowledge --arg s "$1" '{vault:$v, text:., source_label:$s}' "$2" \
  | curl -s -X POST "$GT/ingest?wait=true" -H 'content-type: application/json' -d @-
}

# from a checkout of each repo — READMEs, specs, ADRs, contributor guides
ingest "groundtruth/README"        ~/src/groundtruth/README.md
ingest "groundtruth/requirements"  ~/src/groundtruth/docs/requirements.md
ingest "groundtruth/CLAUDE"        ~/src/groundtruth/CLAUDE.md
ingest "dependaproxy/README"       ~/src/dependaproxy/README.md
ingest "dependaproxy/configuration" ~/src/dependaproxy/docs/configuration.md
ingest "git-proxy/skill"           ~/src/git-proxy/agent/skills/use-git-proxy/SKILL.md
ingest "ai-sandbox/README"         ~/src/ai-sandbox/README.md
ingest "ai-sandbox/use-docker"     ~/src/ai-sandbox/claude-code/use-docker/SKILL.md
# …then your own best-practices notes, one topic per file
```

Watch a job: `curl -s $GT/jobs/<id>`. A finished job reports `commit_sha`,
`notes_created`, `notes_updated`. A `failed` job reports `failure_stage` +
`error` and rolls back — nothing half-written. `deduplicated: true` means that
exact text was already ingested.

Browse the result at `$GT/browse`, or `curl -s "$GT/notes?vault=knowledge"`.

---

## 6. Point agents at it (MCP)

The MCP server is streamable-HTTP at `http://192.168.5.150:30800/mcp`. Tools:
`groundtruth_query`, `groundtruth_ingest`, `list_notes`, `read_note`,
`get_schema`, `update_schema`, `list_vaults`, `job_status`.

Claude Code / Claude Desktop (`.mcp.json` or the desktop config):

```json
{
  "mcpServers": {
    "knowledge": {
      "type": "http",
      "url": "http://192.168.5.150:30800/mcp"
    }
  }
}
```

Tell your project agents, in their `CLAUDE.md` or system prompt:

> Before making a technical decision or writing a PR, query the `knowledge`
> MCP server (`groundtruth_query`) for how this project works, prior decisions,
> and the practices to follow. It answers only from what's been recorded, with
> citations, or it refuses — treat a refusal as "not written down yet" and, if
> the answer matters, `groundtruth_ingest` a note so the next agent has it.

Quick check:

```sh
curl -s -X POST $GT/query -H 'content-type: application/json' \
  -d '{"vault":"knowledge","question":"How does dependaproxy stop a lockfile install from bypassing validation?"}'
```

---

## 7. Operations

**Backup** — the vault is a git repo; the state dir is regenerable.

```sh
kubectl -n groundtruth exec deploy/gt-groundtruth -- \
  tar -C /data -cz knowledge > knowledge-$(date +%F).tgz
# restore: kubectl cp knowledge-*.tgz, tar -xz into /data, restart the pod
```

Better: `kubectl exec … git -C /data/knowledge bundle create - --all > knowledge.bundle`
periodically, or set a real git remote and `auto_push: true`.

**Upgrade** — bump `--version` to the new chart release (and set `image.tag` to
match, or pin it in the values):

```sh
helm upgrade gt oci://ghcr.io/psenna/charts/groundtruth --version <new> \
  -n groundtruth -f values-knowledge.yaml
```

Strategy is `Recreate` (single-writer volume) — expect a few seconds of downtime.

**Logs / health**

```sh
kubectl -n groundtruth logs -f deploy/gt-groundtruth
curl -s $GT/health          # never needs auth
```

---

## Caveats — read before you rely on it

- **Latency.** gemma4:31b at 100k context is slow. `reduce` and `answer` calls
  can take minutes; an ingest of a large doc runs the retrieval agent loop
  several times. `llm_timeout_s: 600` and `max_wall_clock_s: 900` are sized for
  that — if you still get `budget_exhausted` refusals or failed jobs, raise both,
  and ingest smaller chunks. A smaller/faster model for the `tag` role helps a
  lot if you have one (`tag: {model: <small-model>}`).
- **`auth: none`.** The NodePort is open to anything that can reach the node on
  `:30800` — API, web UI, MCP, and `groundtruth_ingest`/`update_schema` writes.
  Fine on a trusted home LAN. To lock it down: see below.
- **Single writer.** One replica, `Recreate`. Do not scale up — the FIFO job
  queue and per-vault git repo assume one process.
- **`allow_schema_writes: true`** lets any MCP client rewrite `schema.md`. Set it
  `false` after the initial curation if you'd rather keep the schema hand-managed.
- **No git remote.** The vault lives only on the PVC (`retainOnDelete: true`, so
  `helm uninstall` keeps it). Take backups.

### Locking it down (optional)

```yaml
config:
  server:
    auth: bearer
    bearer_token_env: GT_BEARER_TOKEN
secret:
  data:
    GT_BEARER_TOKEN: "<long-random-string>"   # or use secret.existingSecret
```

Then every request (API, MCP, **and the web UI**) needs
`Authorization: Bearer <token>` — `/health` stays open. MCP clients add it under
`headers`; browser access to the web UI needs a header-injecting extension.
