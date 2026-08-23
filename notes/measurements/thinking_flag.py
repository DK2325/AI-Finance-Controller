"""Where did 7,950 output tokens go?

The truncated batch billed 8,000 completion tokens and returned 193 characters of text --
one partial entry. Content that short cannot account for that many tokens, so the tokens
are being spent somewhere the `content` field does not show.

The obvious suspect is reasoning. Every prompt declares `enable_thinking: false` and the
provider sends it as `extra_body.chat_template_kwargs.enable_thinking`, which is what the
earlier spike measured as working. If the model is reasoning anyway, then:

*   the 5.4x token measurement that justified turning thinking off is not being realised,
*   every batch is paying for reasoning we explicitly declined,
*   and `max_tokens` is being consumed before the answer starts, which is what truncation
    at 193 characters actually means.

This inspects the whole message object rather than just `content`, at two batch sizes.

    python notes/measurements/thinking_flag.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ledgerloop.config import nvidia_api_key, nvidia_base_url, nvidia_model  # noqa: E402
from llm.prompt import load  # noqa: E402
from notes.measurements.llm_rates import pick_narrations  # noqa: E402


def probe(client, rows, enable_thinking: bool, max_tokens: int) -> dict:
    prompt = load("parse")
    request = prompt.request(rows)

    response = client.chat.completions.create(
        model=nvidia_model(),
        messages=[
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ],
        temperature=0,
        max_tokens=max_tokens,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": request.schema_name, "strict": True,
                            "schema": request.schema},
        },
        extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )

    choice = response.choices[0]
    message = choice.message
    dumped = message.model_dump() if hasattr(message, "model_dump") else {}
    reasoning = dumped.get("reasoning_content") or ""

    return {
        "rows": len(rows),
        "enable_thinking_sent": enable_thinking,
        "max_tokens": max_tokens,
        "finish_reason": choice.finish_reason,
        "completion_tokens": response.usage.completion_tokens,
        "content_chars": len(message.content or ""),
        "reasoning_chars": len(reasoning),
        "message_keys": sorted(k for k, v in dumped.items() if v),
        "reasoning_head": reasoning[:300],
        "content_tail": (message.content or "")[-160:],
        "whitespace_tail_chars": (
            len(message.content or "") - len((message.content or "").rstrip())
        ),
    }


def main() -> None:
    if not nvidia_api_key():
        raise SystemExit("NVIDIA_API_KEY not set")
    from openai import OpenAI

    client = OpenAI(base_url=nvidia_base_url(), api_key=nvidia_api_key(), timeout=300.0)
    pool = pick_narrations(100)

    # Retry the same call until one truncates, then inspect that one fully. A successful
    # call cannot tell us where the tokens went in a failing one.
    report = []
    for attempt in range(6):
        print(f"attempt {attempt + 1} ...", flush=True)
        result = probe(client, pool[20:40], False, 8000)
        report.append({"label": f"attempt {attempt + 1}", **result})
        print(json.dumps({k: v for k, v in result.items() if k != "reasoning_head"}, indent=2))
        if result["finish_reason"] == "length":
            ratio = result["content_chars"] / max(1, result["completion_tokens"])
            print("  CAUGHT A TRUNCATION")
            print(f"  content tail : {result['content_tail']!r}")
            print(f"  chars/token  : {ratio:.3f}")
            break
        print(flush=True)

    Path(__file__).with_name("thinking_flag.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print("written to thinking_flag.json")


if __name__ == "__main__":
    main()
