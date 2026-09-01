# Running groundtruth against a local model

groundtruth talks to any OpenAI-compatible chat-completions endpoint, so a local
[Ollama](https://ollama.com) (or vLLM, llama.cpp `--api`, LM Studio, …) server
works out of the box. Getting *good* ingest and query results out of a local
model, though, takes a few deliberate config choices that a hosted frontier
model would not need. This page collects them.

None of this is a hard requirement — it is tuning. A hosted model behind
`base_url` is usually best left on its provider's defaults; skip to
[What to expect](#what-to-expect-by-model-shape) for how the trade-offs move.

---

## 1. Which model runs which stage

`models` in the config is keyed by **role**. There is one special role,
`default`, and the pipeline uses these role names:

| Pipeline stage | Role it calls | Notes |
|---|---|---|
| survey (agent loop that reads the vault before ingest) | `reduce` | shares the `reduce` role — there is no `survey` role |
| reduce (distil the source text to claims) | `reduce` | |
| tag (per-note, since the note-level tagging change) | `tag` | one call per note written |
| organize (agent loop that writes/updates notes) | `organize` | **not** a declared role — see below |
| query / recovery (answer a question from the vault) | `answer` | |

### How a role's settings are resolved

For every role other than `default`, each missing key is filled from
`models.default`:

- `base_url`, `api_key_env`, `reasoning_effort` — **inherited** from `default`
  when the role does not set them. Set them once on `default`.
- `params` (sampling: `temperature`, `presence_penalty`, …) — **merged**
  key-by-key over `default`'s `params`, so a role adds or overrides individual
  keys without repeating the rest.
- `model` — **not inherited in practice.** `tag`, `reduce`, and `answer` each
  carry a built-in `model:` (`qwen2.5:7b` / `qwen2.5:14b` / `qwen2.5:32b`). If
  you set only `models.default.model` and leave those three alone, **they keep
  running qwen2.5**, not your default model. You must set `model:` on each role
  you want changed.
- `organize` is the exception: it is not a declared role at all, so it falls
  through to `models.default` wholesale — including `default`'s `model`.

So on a local model where you want one model everywhere, you set `model:` **four
times** (default + the three roles), not once.

### A full worked example

One 30B-class local model for everything, thinking off, extraction-friendly
sampling, a bigger tool-call budget for a dense backend:

```yaml
defaults:
  models:
    default:
      base_url: http://host.docker.internal:11434/v1
      model: my-model:30b
      api_key_env: GT_API_KEY
      reasoning_effort: none          # inherited by tag / reduce / answer
      params:                         # merged into every role's params
        temperature: 0.2
        presence_penalty: 0
    tag:    {model: my-model:30b}     # without this line, tag runs qwen2.5:7b
    reduce: {model: my-model:30b}     # without this line, reduce (and survey) run qwen2.5:14b
    answer: {model: my-model:30b}     # without this line, answer runs qwen2.5:32b
    # organize needs no entry — it already uses models.default
  limits:
    max_tool_calls: 50                # see §4
```

Want a stronger model only for answering, and thinking on just for that?

```yaml
    answer:
      model: my-model:70b
      reasoning_effort: high          # overrides default's `none`
      params: {temperature: 0.4}      # merged over default's params (presence_penalty: 0 stays)
```

---

## 2. `reasoning_effort` and `params` on a local model

**`reasoning_effort: none`** on `default` is the recommended start for a local
Qwen3-class model. groundtruth's stages are extraction and tool-calling, not
open-ended reasoning; visible chain-of-thought mostly adds latency and, on
smaller models, leaks reasoning text into tag lists and note bodies. On Ollama,
`none` turns thinking off for Qwen3-class models. `low` / `medium` on those
models do **not** meaningfully shrink the thinking budget — it is on or off.
(On an OpenAI-style hosted endpoint the values are `minimal|low|medium|high` and
mean what the provider says; leave this unset to send nothing.)

**`params: {temperature: 0.2, presence_penalty: 0}}`** on `default`. A
Qwen3-class model ships sampling defaults tuned for creative writing
(`temperature: 1`, `presence_penalty: 1.5`). Against the ingest stages those
defaults visibly hurt: facts get reworded instead of copied, tag output picks
up prose, and a high `presence_penalty` suppresses exactly the repeated precise
terms (product names, identifiers) that should recur. `temperature: 0.2` and
`presence_penalty: 0` is a much better baseline for every ingest role. Bump
`temperature` back up (via a per-role `params` merge) only for `answer`, and
only a little.

This is a local-model concern. A hosted model usually performs best on its
provider's own defaults — do not copy these values onto a hosted `answer` role
without checking.

---

## 3. Context window vs. speed

Ingest prompts are not large. Even a long source document plus the full
vault note-path list and the derived tag vocabulary rarely exceeds ~15k tokens.
The agent-loop transcripts (survey, organize) grow with each tool call —
`grep` results are capped at `grep_max_bytes` (64 KiB) and file reads at
`read_max_bytes` (32 KiB) per call — but they are never truncated mid-run, so
the practical ceiling is roughly `read_max_bytes × max_tool_calls`.

On a **dense** local model, prefill time scales with the context window you
configure, so an oversized window is pure latency. A **32k** context window is
usually enough for groundtruth and is dramatically faster than 100k on the same
hardware. Only raise it if you actually see a stage fail on context length with
a genuinely huge source document.

Note that setting `num_ctx` through `params` is unreliable across backends —
some honour it per request, some ignore it in favour of the model's `Modelfile`
or server flag. If window size matters to you, set it at the server
(`ollama` `Modelfile` / `OLLAMA_CONTEXT_LENGTH`, vLLM `--max-model-len`) and
treat the config `params` route as best-effort.

---

## 4. `max_tool_calls` for dense vs. MoE models

`limits.max_tool_calls` bounds the survey and organize agent loops (each tool
call is charged against the budget; the loop also has a hard iteration cap of
`2 × max_tool_calls + 10`). The default is **30**.

- **MoE models** (a mixture-of-experts backend, few active parameters per token)
  are fast per call and tend to converge within the default. 30–40 is fine.
- **Dense models** are slower per call and also tend to explore the vault more
  before writing. On real-world documents they frequently trip the budget at
  30. Raise `max_tool_calls` to **40–60** for a dense local model; go higher if
  you see jobs failing with `organize budget exhausted` / `survey budget
  exhausted` rather than a validation error.

Raising `max_tool_calls` also raises the worst-case wall-clock per job, so keep
`max_wall_clock_s` and `llm_timeout_s` in step with it. `llm_timeout_s` is the
HTTP timeout for a *single* call — a slow dense model doing a 100k-token prefill
can blow a 60s timeout on its own; 120–180s is safer locally.

---

## 5. What to expect by model shape

Framed by the *shape* of the backend, not specific tags (which drift):

| Shape | Ingest quality | Speed | Backend stability | Good for |
|---|---|---|---|---|
| **Hosted frontier** (behind `base_url`) | Best — clean extraction, few validator retries, reliable tool calls | Network-bound, consistent | Very high | The baseline; use it if cost allows |
| **Local dense, large (~30B+)** | Good — close to hosted on extraction; occasional over-splitting of notes | Slow per call; prefill-heavy; a full batch can take many minutes | Good, but large dense models push VRAM and can hit backend OOM / driver faults under load | Best local quality when you have the VRAM and patience |
| **Local MoE (large total, few active params)** | Solid — a bit more likely to reword facts or need an organize retry | Fast per call — often several× a dense model of similar quality | Good; lighter VRAM pressure than a dense model of similar quality | The pragmatic local default: most of the quality, a fraction of the wall-clock |
| **Local small dense (≤14B)** | Weak — frequent validator rejections, dangling wikilinks, prose in tags, placeholder bodies | Fast | High | Smoke-testing the deployment, not production ingest |

General rules of thumb:

- Quality is dominated by the model, not by these knobs — the knobs stop a
  capable model from underperforming, they do not lift a weak one.
- If a local model produces *structurally* bad output (prose in tag lists,
  frontmatter in note bodies, near-duplicate notes), tune §2 first.
- If jobs *fail* rather than produce bad notes, it is usually the budget (§4)
  or a timeout (§4), not sampling.
- MoE backends are the sweet spot for local ingest today: the per-call speed
  advantage compounds across the survey and organize loops.

---

## See also

- [`config.yaml.example`](../config.yaml.example) — every config key with an
  inline comment; the `models:` block mirrors §1.
- [`charts/groundtruth/values.yaml`](../charts/groundtruth/values.yaml) — the
  same structure for the Helm chart.
- [`docs/requirements.md`](requirements.md) §4.3 (per-role LLM client), §7.4
  (agent-loop budget), §11.2 (config precedence).
- The job detail view in the web UI shows per-stage token usage and wall-clock,
  which is the fastest way to see which stage a local model is spending its time
  and budget on.
