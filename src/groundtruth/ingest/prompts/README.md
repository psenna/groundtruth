# Ingestion prompts

Three role prompts, one Markdown file each, versioned with the code:

| Role | File | Output |
|---|---|---|
| `tag` | `tag.md` | normalized tags, one per line |
| `reduce` | `reduce.md` | kept claims/facts/relationships, one per line |
| `organize` | `organize.md` | `create_note` / `update_note` tool calls only |

Placeholders are `{{UPPER_SNAKE}}` and filled by `render_prompt(role, **context)`:
`{{SCHEMA_MD}}` (verbatim, prescriptive — §5.2), `{{DERIVED_VOCABULARY}}` (descriptive,
from `derive_vocabulary`, §5.3), `{{INPUT_TEXT}}` / `{{INPUT_ITEMS}}`,
`{{EXISTING_NOTES}}`.

## Iterating on quality

The reduce step is the product (spec open question 3) and will take several passes.
**Do not tune it by eyeballing single prompts — use the golden eval (#39) as the
measuring instrument.** The manual loop, for spot checks only:

1. `uv run pytest tests/eval` against the fixture vault (#39).
2. Run an ingest of a fixture document against a scratch clone of the fixture vault.
3. `git diff` the vault: are the notes ones a human would want to query? Too many notes
   (per-claim, not per-topic — ADR-9)? Narration kept? Restatement kept?
4. Edit the prompt file, re-run, re-diff. Commit the prompt change with the eval delta.
