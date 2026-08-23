"""What is actually in the truncated response?

I measured the truncation rate three times over before looking at a single byte of the
output that was truncating. That was the wrong order: a rate tells you how often, and the
text tells you why, and the text is one call away.

Sends the batch that truncates deterministically and dumps the raw response.

    python notes/measurements/truncation_dump.py
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llm.prompt import load  # noqa: E402
from llm.provider import get_provider  # noqa: E402
from notes.measurements.llm_rates import pick_narrations  # noqa: E402


def main() -> None:
    choice = get_provider()
    if choice.provider.name != "nvidia":
        raise SystemExit(f"expected the live provider, got {choice.provider.name}")

    rows = pick_narrations(100)[20:40]
    prompt = load("parse")
    response = choice.provider.complete(prompt.request(rows))

    print(f"model      {response.model}")
    print(f"output     {response.output_tokens} tokens, {len(response.text)} chars")
    print(f"truncated  {response.truncated}\n")

    print("--- first 1200 chars " + "-" * 55)
    print(response.text[:1200])
    print("\n--- last 900 chars " + "-" * 57)
    print(response.text[-900:])

    # If it is a loop, some fragment repeats far more than the 20 entries justify.
    entries = re.findall(r'"id"\s*:\s*"([^"]+)"', response.text)
    print("\n--- structure " + "-" * 62)
    print(f'"id" fields emitted : {len(entries)}   (20 were sent)')
    repeats = Counter(entries).most_common(5)
    print(f"most repeated ids   : {repeats}")

    lines = [ln.strip() for ln in response.text.splitlines() if ln.strip()]
    print(f"most repeated lines : {Counter(lines).most_common(3)}")

    Path(__file__).with_name("truncation_dump.json").write_text(
        json.dumps(
            {
                "model": response.model,
                "output_tokens": response.output_tokens,
                "chars": len(response.text),
                "truncated": response.truncated,
                "ids_emitted": len(entries),
                "ids_sent": len(rows),
                "most_repeated_ids": repeats,
                "text": response.text,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("\nwritten to truncation_dump.json")


if __name__ == "__main__":
    main()
