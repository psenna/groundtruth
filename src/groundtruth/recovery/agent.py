"""Recovery agent orchestration (spec §8.1, §8.2, §8.5).

Question in, grounded answer out. The agent reads ``schema.md`` first as the map
of the vault, then searches with the **read-only** tools from #13. Recovery never
writes — no note, no ``schema.md``, no commit (invariant 1). No write tool is
importable in this module, so there is nothing to enforce by convention.

Every call takes an explicit ``Vault``; nothing assumes a single vault (§8.5).
"""

from __future__ import annotations

from ..config import Limits
from ..ingest.schema import load_schema
from ..models import Vault
from ..retrieval.agent import AgentOutcome, LLMLike, run_agent
from ..retrieval.budget import Budget, BudgetLimits
from ..retrieval.tools import ReadOnlyTools

_RECOVERY_PROMPT = """\
You answer a question using ONLY what this vault contains. You never use outside
knowledge: if the vault does not contain the answer, say so plainly.

## The vault's schema (its map)

{schema_md}

## Rules

- Search with `ls`, `grep`, and `read`. You have no other tools.
- Every substantive claim in your answer MUST carry a `[[note path]]` citation
  pointing at the note it came from.
- If you cannot find the answer in the vault, respond that the vault does not
  contain it. Do not guess and do not fill the gap from general knowledge.

## Question

{question}
"""


def recover(
    vault: Vault,
    question: str,
    client: LLMLike,
    *,
    limits: Limits | None = None,
) -> AgentOutcome:
    """Run the recovery agent loop for ``question`` against ``vault``.

    Returns an :class:`AgentOutcome` — ``completed`` with the answer text,
    ``exhausted`` when the budget runs out (never an exception), or ``failed``.
    """
    schema = load_schema(vault.vault_dir)
    budget = Budget(BudgetLimits.from_limits(limits) if limits is not None else BudgetLimits())
    tools = ReadOnlyTools(vault.vault_dir, budget)
    prompt = _RECOVERY_PROMPT.format(schema_md=schema.raw, question=question)
    return run_agent(client, "answer", prompt, tools, budget)


__all__ = ["recover"]
