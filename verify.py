# SPDX-License-Identifier: Apache-2.0
# boto3 ESBMC-Python verification orchestrator.
#
# Single source of truth for: target name -> entry script -> ESBMC args
# -> expected verdict, per phase.
#
# Phases (matches the vLLM / AWS-Neuron / chromium-dashboard PoCs):
#   Phase 1: default flags. Functional contracts via `assert`.
#   Phase 2: --overflow-check. CWE-190 / CWE-369 on host integer math.
#
# A target with `safety_expected=None` skips Phase 2 (used for buggy
# variants and live-bug witnesses whose Phase 1 already FAILS).

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.abspath(__file__))
HARNESS_DIR = os.path.join(ROOT, "harness")
ESBMC = os.environ.get("ESBMC", "esbmc")

# Phase-2 base flag set. Targets may append their own.
_SAFETY: tuple[str, ...] = ("--overflow-check",)


@dataclass
class Target:
    name: str
    entry: str                                  # filename under harness/
    esbmc_args: tuple[str, ...] = ()            # extra Phase-1 args
    expected: str | None = "SUCCESSFUL"         # Phase-1 verdict, None to skip
    safety_args: tuple[str, ...] = _SAFETY      # extra Phase-2 args
    safety_expected: str | None = "SUCCESSFUL"  # Phase-2 verdict, None to skip


TARGETS: list[Target] = [
    # --- Tier 1: pure predicates and helpers -----------------------
    Target(
        # boto3/resources/response.py:20 -- the None-vs-falsy predicate
        # whose contract is the antidote to the Tier-2 bug class.
        name="all_not_none",
        entry="all_not_none.py",
        esbmc_args=("--unwind", "5"),
        expected="SUCCESSFUL",
        safety_expected="SUCCESSFUL",
    ),
    Target(
        # Truthiness mutation: `if not element` instead of
        # `if element is None`. Rejects the legal values 0 / False /
        # '' / [] that upstream deliberately admits.
        name="all_not_none_buggy",
        entry="all_not_none_buggy.py",
        esbmc_args=("--unwind", "5"),
        expected="FAILED",
        safety_expected=None,
    ),
    # --- Tier 2: silent acceptance of unvalidated parameters -------
    Target(
        # Finding A witness: ResourceCollection.limit(n) for n <= 0.
        # boto3/resources/collection.py:76-87 (__iter__) and
        # :168-184 (pages()). Both loops count the item BEFORE testing
        # the limit, so a non-positive limit yields exactly one item
        # instead of zero. Phase 1 FAILED is the expected and
        # significant verdict -- the counterexample IS the bug report.
        name="collection_limit_nonpositive",
        entry="collection_limit_nonpositive.py",
        esbmc_args=("--unwind", "6"),
        expected="FAILED",
        safety_expected=None,
    ),
    Target(
        # Positive control modelling the proposed fix (test the limit
        # before yielding). Confirms the Finding A harness shape is
        # non-vacuous: same loop, corrected guard, SUCCESSFUL.
        name="collection_limit_honored",
        entry="collection_limit_honored.py",
        esbmc_args=("--unwind", "6"),
        expected="SUCCESSFUL",
        safety_expected="SUCCESSFUL",
    ),
    # --- Tier 3: bare exceptions at the API boundary ---------------
    Target(
        # Finding G witness: EC2 create_tags with a tag omitting Value.
        # ec2/createtags.py:38 dereferences tag['Key'] / tag['Value']
        # unguarded, AFTER the CreateTags request has been issued at
        # :27. The EC2 `Tag` shape has no required members, so botocore
        # cannot reject the call. Phase 1 FAILED is the expected
        # verdict -- the counterexample IS the bug report.
        name="create_tags_missing_value",
        entry="create_tags_missing_value.py",
        esbmc_args=("--unwind", "4"),
        expected="FAILED",
        safety_expected=None,
    ),
    Target(
        # Positive control modelling the proposed fix
        # (tag.get('Value', ''), the value EC2 itself stores). Confirms
        # the Finding G harness is non-vacuous: same admitted input
        # domain, same ordering invariant, SUCCESSFUL.
        name="create_tags_missing_value_fixed",
        entry="create_tags_missing_value_fixed.py",
        esbmc_args=("--unwind", "4"),
        expected="SUCCESSFUL",
        safety_expected="SUCCESSFUL",
    ),
]



def _verdict_from_output(out: str) -> str:
    if "VERIFICATION SUCCESSFUL" in out:
        return "SUCCESSFUL"
    if "VERIFICATION FAILED" in out:
        return "FAILED"
    return "ERROR"


# Match ESBMC's per-run VCC accounting line, e.g.:
#   "Generated 3 VCC(s), 3 remaining after simplification (12 assignments)"
_VCC_RE = re.compile(r"Generated (\d+) VCC\(s\)")


def _vcc_count(out: str) -> int | None:
    """Return the 'Generated N VCC(s)' integer, or None if absent."""
    m = _VCC_RE.search(out)
    return int(m.group(1)) if m else None


def _run_esbmc(entry: str, args: tuple[str, ...]) -> tuple[str, int | None, str]:
    cmd = [ESBMC, *args, entry]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, cwd=HARNESS_DIR, check=False
    )
    output = proc.stdout + proc.stderr
    return _verdict_from_output(output), _vcc_count(output), output[-400:]


def _run_target(target: Target, phases: tuple[int, ...]) -> int:
    failures = 0
    for phase in phases:
        if phase == 1:
            expected = target.expected
            extra_args = target.esbmc_args
        else:
            expected = target.safety_expected
            extra_args = target.esbmc_args + target.safety_args
        if expected is None:
            print(f"  [Phase {phase}] skipped")
            continue
        verdict, vcc, tail = _run_esbmc(target.entry, extra_args)

        # Vacuity guard: a SUCCESSFUL verdict with 0 VCCs generated means
        # symbolic execution never reached any user-level assertion -- the
        # harness passes vacuously and proves nothing. Inherited from the
        # vLLM PoC (RETROSPECTIVE.md Finding 1), where a runtime `def
        # nondet_int()` shadowed the ESBMC intrinsic and silently sliced
        # away every assertion.
        vacuous = verdict == "SUCCESSFUL" and vcc == 0
        ok = verdict == expected and not vacuous

        if vacuous:
            marker = "FAIL (vacuous: 0 VCCs)"
        elif ok:
            marker = "PASS"
        else:
            marker = "FAIL"

        vcc_str = "?" if vcc is None else str(vcc)
        cmd_str = shlex.join((ESBMC, *extra_args, target.entry))
        print(
            f"  [Phase {phase}] {marker}: "
            f"verdict={verdict} vcc={vcc_str} expected={expected}  ({cmd_str})"
        )
        if not ok:
            failures += 1
            print(f"      tail: {tail!r}")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=("1", "2", "all"), default="all")
    ap.add_argument("--only", nargs="*", default=None,
                    help="restrict to target names")
    args = ap.parse_args()

    phases: tuple[int, ...] = (1, 2) if args.phase == "all" else (int(args.phase),)

    selected = TARGETS
    if args.only:
        selected = [t for t in TARGETS if t.name in set(args.only)]
        if not selected:
            print(f"no matching targets in {args.only!r}", file=sys.stderr)
            return 2

    total_failures = 0
    for t in selected:
        print(f"== {t.name} ==")
        total_failures += _run_target(t, phases)
    print()
    print(f"total failures: {total_failures}")
    return 1 if total_failures else 0


if __name__ == "__main__":
    sys.exit(main())
