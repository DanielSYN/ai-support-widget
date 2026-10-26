"""Checks how the real model answers the demo shop's customers.

    LLM_MODE=claude python -m evals.run_eval

Each case is one customer question with what a good reply looks like:
  answer   - answered from the knowledge base and mentions one of `any_of`
  decline  - says it doesn't know instead of inventing a policy
  human    - offers a person
  any      - only the `none_of` check applies (e.g. prompt-extraction attempts)
Every reply must also avoid all `none_of` phrases. Costs a few cents per run.
"""

import json
import sys
from pathlib import Path

from app import config, llm
from app.seed import DEMO_EXTRA_INFO, DEMO_FAQS

CASES = json.loads((Path(__file__).parent / "cases.json").read_text())


def grade(case: dict, reply: llm.Reply) -> list[str]:
    problems = []
    text = reply.text.lower()
    expect = case["expect"]
    if expect == "answer":
        if not reply.answered:
            problems.append("said it didn't know")
        if not any(phrase.lower() in text for phrase in case["any_of"]):
            problems.append(f"missing any of {case['any_of']}")
    elif expect == "decline" and reply.answered:
        problems.append("claimed to answer from the knowledge base")
    elif expect == "human" and not reply.needs_human:
        problems.append("didn't offer a person")
    problems += [f"contains {phrase!r}" for phrase in case.get("none_of", []) if phrase.lower() in text]
    return problems


def main() -> int:
    if config.LLM_MODE != "claude":
        print("Set ANTHROPIC_API_KEY (or LLM_MODE=claude) to evaluate the real model.")
        return 2

    bot = {"name": "Trailhead Outfitters", "extra_info": DEMO_EXTRA_INFO}
    faqs = [{"question": q, "answer": a} for q, a in DEMO_FAQS]
    system_prompt = llm.build_system_prompt(bot, faqs)

    failures = 0
    for case in CASES:
        reply = llm.claude_reply(system_prompt, [{"role": "user", "content": case["question"]}])
        problems = grade(case, reply)
        failures += bool(problems)
        print(f"{'FAIL' if problems else 'pass'}  [{case['expect']:7}] {case['question']}")
        print(f"        -> {reply.text}")
        for problem in problems:
            print(f"        !! {problem}")

    passed = len(CASES) - failures
    print(f"\n{passed}/{len(CASES)} passed ({passed / len(CASES):.0%}) on {config.CLAUDE_MODEL}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
