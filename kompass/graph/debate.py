"""Debate panel: independent opinions from distinct lenses, synthesized by a judge.

For a genuinely ambiguous decision — a borderline refund, an edge-case policy call — one model
opinion is fragile. This runs N independent opinions, each from a distinct stance (enforce the
rules literally, favor the customer within policy, weigh abuse signals), then a reasoning-tier
judge weighs them into a final verdict. Adversarial verification for decision quality: the lenses
argue past each other on purpose, so their disagreement is itself signal for the judge.

The panel calls are independent — no lens sees another's answer. `tally` is the pure vote count
that both the judge prompt and the tests rely on. Callers use `adjudicate`; the `__main__` block
demos one borderline case.
"""

import sys
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from kompass.models.router import pick


@dataclass(frozen=True)
class Lens:
    """A panelist: a name and the system stance it argues the case from."""

    name: str
    stance: str


STRICT_POLICY = Lens(
    "strict-policy",
    "You are a strict policy officer on a support decision panel. Apply ACME's written rules "
    "to the letter — sympathy, goodwill, and retention are not your concern; eligibility is. "
    "If the request falls outside the stated window or conditions, deny it; approve only when "
    "the rules plainly allow it, and escalate only when the rules are silent or contradict.",
)

CUSTOMER_ADVOCATE = Lens(
    "customer-advocate",
    "You are a customer advocate on a support decision panel. Within what policy permits, "
    "resolve doubt in the customer's favor and protect the relationship. Look hard for a "
    "legitimate, defensible basis to approve — an applicable exception, a fair reading of an "
    "ambiguous rule. Never invent grounds the policy forbids; when there is genuinely no basis "
    "to approve, say so and prefer escalation over a false promise.",
)

FRAUD_RISK = Lens(
    "fraud-risk",
    "You are a fraud and abuse analyst on a support decision panel. Weigh the abuse signals: "
    "claims filed just outside a verifiable window, opened packaging with no reported damage, "
    "vague or shifting details, patterns that suggest serial refunding. If the abuse signals "
    "are material, lean toward deny or escalate for verification. If there are no real red "
    "flags, do not obstruct a legitimate request.",
)

LENSES = (STRICT_POLICY, CUSTOMER_ADVOCATE, FRAUD_RISK)


class Opinion(BaseModel):
    """One lens's independent call on the decision."""

    position: Literal["approve", "deny", "escalate"] = Field(
        description="this lens's call on the decision"
    )
    reason: str = Field(description="one or two sentences justifying the position from this stance")


class Verdict(BaseModel):
    """The judge's synthesized final decision."""

    decision: Literal["approve", "deny", "escalate"] = Field(
        description="the panel's final, adjudicated decision"
    )
    confidence: float = Field(description="confidence in the decision, from 0 (a toss-up) to 1")
    rationale: str = Field(description="how the opinions and evidence were weighed into it")


OPINION_TASK = """Decide this customer-support case from your assigned perspective. Choose one
position — approve, deny, or escalate — and give a one- or two-sentence reason grounded in the
facts.

Decision: {question}

Context:
{context}"""

JUDGE = """You are the presiding judge of a support decision panel. Three advisers each reviewed
the same case independently, from a different stance. Weigh their opinions against one another and
against the evidence, then deliver the final decision.

Return the decision (approve, deny, or escalate), your confidence as a number in [0, 1], and a
rationale that explains how you weighed the panel — especially where they disagreed. A split panel
or a genuinely under-evidenced case is itself a reason to escalate rather than guess.

Decision: {question}

Context:
{context}

Vote tally: {tally}

Panel opinions:
{opinions}"""


def tally(opinions: list[Opinion]) -> dict[str, int]:
    """Count how many panelists landed on each position — a deterministic vote tally."""
    counts = {"approve": 0, "deny": 0, "escalate": 0}
    for opinion in opinions:
        counts[opinion.position] += 1
    return counts


def _gather(question: str, context: str, panel: tuple) -> list[tuple[Lens, Opinion]]:
    """Collect one independent opinion per lens — each an isolated balanced-tier call."""
    task = OPINION_TASK.format(question=question, context=context or "(none provided)")
    model = pick("balanced").with_structured_output(Opinion)
    return [(lens, model.invoke([("system", lens.stance), ("user", task)])) for lens in panel]


def _judge(question: str, context: str, opinions: list[tuple[Lens, Opinion]]) -> Verdict:
    """Synthesize the panel's opinions into one final verdict — a reasoning-tier call."""
    rendered = "\n".join(f"- [{lens.name}] {op.position}: {op.reason}" for lens, op in opinions)
    prompt = JUDGE.format(
        question=question,
        context=context or "(none provided)",
        tally=tally([op for _, op in opinions]),
        opinions=rendered,
    )
    return pick("reasoning").with_structured_output(Verdict).invoke(prompt)


def adjudicate(question: str, context: str = "", panel: tuple = LENSES) -> Verdict:
    """Run each lens as an independent opinion, then synthesize the panel into a Verdict.

    Each panelist in `panel` answers the same case in isolation from a distinct system stance
    (a balanced-tier structured call); a reasoning-tier judge then weighs every opinion, the vote
    tally, and the context into one adjudicated decision. Adversarial verification for a borderline
    call: independent lenses surface the side a single opinion would have missed.
    """
    return _judge(question, context, _gather(question, context, panel))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    RULE = "=" * 88

    question = (
        "Customer wants a full refund on order 4462 (delivered 2026-05-22, ~43 days ago, "
        "no damage reported, packaging opened). Approve?"
    )
    context = (
        "ACME policy: refunds within 30 days of delivery. Damaged or defective items may be "
        "refunded beyond the window with evidence; opened packaging alone is not damage."
    )

    opinions = _gather(question, context, LENSES)
    print(RULE)
    print(f"CASE  {question}\n")
    for lens, op in opinions:
        print(f"  [{lens.name}] {op.position.upper()} — {op.reason}")
    print(f"\n  tally: {tally([op for _, op in opinions])}")

    verdict = _judge(question, context, opinions)
    print(RULE)
    print(f"VERDICT  {verdict.decision.upper()}  (confidence {verdict.confidence:.2f})")
    print(f"  {verdict.rationale}")
    print(RULE)
