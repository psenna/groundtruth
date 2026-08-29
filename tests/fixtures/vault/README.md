# Fixture vault (golden eval, spec §9.2)

`vault/` is a ~17-note knowledge base with a real `schema.md`. Its contents are
fictional and self-contained. It backs `tests/integration/test_grounding_eval.py`.

## Adding a case

Edit `tests/integration/test_grounding_eval.py`:

- **Answerable** — append to `ANSWERABLE` with the question, a substring the
  answer must contain, and the note it must cite. Only add facts that are
  actually in `vault/`.
- **Must-refuse** — append to `MUST_REFUSE` with a question whose answer is *not*
  in `vault/`. Prefer facts a pretrained model plausibly "knows" (a real
  company's founding year, a city's population): a correct-but-ungrounded answer
  is a **failure**.

## Running against a real model

The default run uses a scripted fake model and is deterministic (no network).
To run the same cases against a live OpenAI-compatible model:

```
GT_EVAL_MODEL_BASE_URL=http://localhost:11434/v1 \
GT_EVAL_MODEL=qwen2.5:14b \
GT_EVAL_API_KEY_ENV=GT_API_KEY \
uv run pytest tests/integration/test_grounding_eval.py -m eval_live
```
