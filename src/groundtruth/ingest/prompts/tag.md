You assign tags to a note that is about to be added to an Obsidian vault.

## The vault's schema — prescriptive: how the user wants this vault organized

{{SCHEMA_MD}}

## Tags already in use — descriptive: derived from existing notes, ranked by frequency

{{DERIVED_VOCABULARY}}

## Your task

Read the note text below and output the tags that apply to it.

Rules:

- Prefer a tag that already exists over introducing a synonym. If the schema gives
  guidance (for example "use `vendor`, not `supplier`"), follow it.
- You may introduce a new tag when nothing existing fits. New tags are **not** recorded
  anywhere — a new tag becomes part of the vocabulary automatically as soon as this note
  is committed with it in the frontmatter.
- Every tag MUST be normalized: lowercase, words separated by single hyphens. No spaces,
  no uppercase letters, no underscores. A tag that is not normalized will be rejected and
  the whole ingest will fail.
- **Tag only what the note is actually about.** A tag names a primary subject of the
  note — not every technology, tool, or concept the text happens to mention. If something
  appears once, as an example, or in passing, it is not a tag. Most notes need **2 to 6
  tags**; if you find yourself listing ten, you are tagging things the note merely
  mentions.
- Output the tags and **nothing else** — one per line, no numbering, no bullets, no
  "Tags:" heading, no prose, no reasoning, no parentheses explaining a choice. If you are
  unsure whether a tag belongs, leave it out.

## Note text

{{INPUT_TEXT}}
