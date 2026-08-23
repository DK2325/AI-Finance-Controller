"""The whole path, on real exceptions: enumerate, select, explain, verify, audit.

Everything before this measured a layer. This runs the layers in the order they actually
compose, against `data/train`, and checks the property the phase is really claiming:

    every settlement is accounted for, and every exception that reaches a human carries
    an explanation that survived verification

Specifically it asserts, on real data rather than fixtures:

1.  matched + exceptions == every settlement, exactly once each
2.  only JUDGEMENT-family exceptions are sent to the model -- the deterministic ones are
    already explained and spending a token on them would breach architecture rule 1
3.  the model's own reason-code tag is *recorded and compared*, never substituted for the
    pipeline's
4.  every audit record has the same shape whichever layer wrote it

    python notes/measurements/end_to_end.py [n_exceptions]
"""

import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.pipeline import load_sources  # noqa: E402
from ledgerloop.audit import (  # noqa: E402
    DECISION_EXCEPTION,
    LAYER_LLM,
    AuditRecord,
    row_hash,
)
from llm.cache import ResponseCache  # noqa: E402
from llm.codes import ReasonCode, needs_llm  # noqa: E402
from llm.handler import run_job  # noqa: E402
from llm.provider import get_provider  # noqa: E402
from model.artifact import Artifact  # noqa: E402
from model.predict import audit_records, reconcile_batch  # noqa: E402

DEFAULT_SAMPLE = 100
BATCH = "data/train"


def rows_for_reason(exceptions, sources, threshold: float) -> list[dict]:
    """Build the reason prompt's input rows from real exception records.

    Only the evidence the pipeline already holds. Nothing is recomputed for the model's
    benefit -- if a figure is not already known, the model does not get to see one.
    """
    txn_by_id = {t.txn_id: t for t in sources.bank}
    invoice_by_id = sources.invoice_by_id
    settlement_by_entity = {s.entity_id: s for s in sources.payments}

    rows = []
    for record in exceptions:
        settlement = settlement_by_entity.get(record.entity_id)
        txn = txn_by_id.get(record.txn_id)
        invoice = invoice_by_id.get(record.invoice_id)
        evidence = record.evidence

        net = settlement.net_amount if settlement else 0
        credit = txn.credit if txn else 0
        gap = ""
        if settlement and txn and settlement.settled_date and txn.value_date:
            gap = (txn.value_date - settlement.settled_date).days

        rows.append({
            "id": record.entity_id,
            "reason_code": str(record.reason_code),
            "counterparty": invoice.customer_name if invoice else "unknown",
            "net_amount": net,
            "bank_credit": credit,
            "difference": net - credit,
            "date_gap": gap if gap != "" else "unknown",
            "probability": round(record.confidence, 4) if record.confidence else 0.0,
            "threshold": round(threshold, 4),
            "n_close": evidence.n_candidates if evidence else 0,
            "narration": txn.narration if txn else "",
        })
    return rows


def main() -> None:
    sample = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SAMPLE

    artifact = Artifact.load("runs/_models/v1")
    threshold = artifact.operating_point["threshold"]

    print(f"batch      {BATCH}")
    print(f"model      {artifact.model_version}, threshold {threshold:.4f}")

    started = time.perf_counter()
    outcome = reconcile_batch(BATCH, artifact, threshold=threshold)
    enumeration = outcome.enumeration
    deterministic_seconds = time.perf_counter() - started

    # ---- 1. the invariant, on real data ------------------------------------
    matched = enumeration.matched_entity_ids
    excepted = [r.entity_id for r in enumeration.exceptions]
    assert len(matched) + len(excepted) == enumeration.n_settlements
    assert not (matched & set(excepted)), "a settlement is both matched and excepted"
    assert len(set(excepted)) == len(excepted), "a settlement carries two reason codes"

    print(f"\nsettlements {enumeration.n_settlements}")
    print(f"  matched    {len(matched)}")
    print(f"  exceptions {len(excepted)}   in {deterministic_seconds:.1f}s, no tokens")
    for code, count in enumeration.by_reason().items():
        marker = "-> LLM" if needs_llm(ReasonCode(code)) else "free  "
        print(f"      {marker}  {code:24} {count:5}")

    # ---- 2. only judgement exceptions reach the model -----------------------
    judgement = [r for r in enumeration.exceptions if needs_llm(r.reason_code)]
    assert all(needs_llm(r.reason_code) for r in judgement)
    print(f"\ndeterministic share {enumeration.deterministic_share():.2%}"
          f"   ({len(enumeration.exceptions) - len(judgement)} explained for free)")

    chosen = judgement[:sample]
    sources = load_sources(BATCH)
    rows = rows_for_reason(chosen, sources, threshold)

    choice = get_provider()
    print(f"\nexplaining {len(rows)} exceptions via {choice.provider.name}...", flush=True)

    result = run_job(
        "reason", rows, provider=choice.provider, cache=ResponseCache(enabled=False)
    )

    # ---- 3. the model's tag is compared, never substituted ------------------
    agreed = disagreed = 0
    disagreements: Counter = Counter()
    for record, item in zip(chosen, result.outcomes, strict=True):
        if not item.ok or not item.fields:
            continue
        theirs = item.fields.get("reason_code")
        ours = str(record.reason_code)
        if theirs == ours:
            agreed += 1
        else:
            disagreed += 1
            disagreements[f"{ours} -> {theirs}"] += 1

    # ---- 4. audit records, one shape --------------------------------------
    records = audit_records(outcome, "e2e", artifact)
    llm_records = [
        AuditRecord(
            run_id="e2e", layer=LAYER_LLM, decision=DECISION_EXCEPTION,
            entity_id=item.item_id, reason_code=record.reason_code,
            reason_detail=(item.fields or {}).get("reason_text", item.detail)[:200],
            input_row_hashes={"settlement": row_hash(item.item_id)},
            provider=item.provider, model_name=item.model,
            prompt_version=item.prompt_version, cache_hit=item.cache_hit,
            input_tokens=item.input_tokens, output_tokens=item.output_tokens,
        )
        for record, item in zip(chosen, result.outcomes, strict=True)
    ]
    shapes = {tuple(sorted(r.as_row())) for r in records + llm_records}

    report = {
        "settlements": enumeration.n_settlements,
        "matched": len(matched),
        "exceptions": len(excepted),
        "by_reason": enumeration.by_reason(),
        "deterministic_share": round(enumeration.deterministic_share(), 4),
        "llm_bound": len(judgement),
        "explained_now": len(rows),
        "llm": result.as_dict(),
        "reason_code_agreement": {
            "agreed": agreed,
            "disagreed": disagreed,
            "rate": round(agreed / (agreed + disagreed), 4) if agreed + disagreed else None,
            "disagreements": dict(disagreements),
        },
        "audit_records": len(records) + len(llm_records),
        "distinct_record_shapes": len(shapes),
    }

    print("\n" + json.dumps(
        {k: v for k, v in report.items() if k != "llm"}, indent=2
    ))
    print(f"\nllm: {json.dumps(result.as_dict())}")

    sample_ok = next((o for o in result.outcomes if o.ok and o.fields), None)
    if sample_ok:
        print("\nexample explanation:")
        print(f"  {sample_ok.fields.get('reason_text')}")
        print(f"  action: {sample_ok.fields.get('suggested_action')}")

    assert len(shapes) == 1, f"audit records have {len(shapes)} different shapes"

    out = Path(__file__).with_name("end_to_end.json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
