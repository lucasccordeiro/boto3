# SPDX-License-Identifier: Apache-2.0
# Canonical stub library for the boto3 ESBMC-Python PoC.
#
# Imported by every entry script in harness/. Edit only this file to
# change a stub contract.
#
# Philosophy (inherited from the AWS-Neuron / vLLM PoCs):
#   - Model only what a target *reads*. Do not invent behaviour.
#   - Preconditions are `__ESBMC_assume(...)`. Postconditions are
#     plain `assert ...`.
#
# **DO NOT** define `nondet_int` / `nondet_bool` / `__ESBMC_assume` as
# runtime Python functions here. Those names are ESBMC-Python
# intrinsics; a runtime def makes ESBMC use the Python body (e.g.
# `return 0` for nondet_int), collapsing the symbolic value to a
# constant, turning every `__ESBMC_assume(...)` into a no-op, and
# letting every `assert` be sliced away -- ESBMC then reports
# VERIFICATION SUCCESSFUL with 0 VCCs generated. verify.py's vacuity
# guard exists to catch exactly this (vLLM RETROSPECTIVE.md Finding 1).
#
# The `TYPE_CHECKING`-guarded declarations below give static checkers
# the type information they need without putting executable code in
# ESBMC's path: `TYPE_CHECKING` is False at runtime, so the bodies are
# unreachable and ESBMC keeps the intrinsic resolution.

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # ESBMC-Python intrinsics. Declared only for static type checkers.
    # ESBMC provides the symbols at verification time; CPython raises
    # NameError at import (intentional -- harness files are
    # verifier-only and are never executed under CPython).
    def nondet_int() -> int: ...
    def nondet_bool() -> bool: ...
    def __ESBMC_assume(_c: bool) -> None: ...  # noqa: N802


# --- Concrete bounds used by entry scripts -------------------------
#
# Two bounds because postcondition shape determines tractability:
#
#   INT_BOUND   = 1 << 30 -- wide window for properties Bitwuzla
#                            handles linearly (assumptions, a single
#                            division, equality on a derived value).
#   SMALL_BOUND = 1 << 10 -- for postconditions with non-linear
#                            arithmetic in the symbolic inputs. Covers
#                            every realistic boto3 call site: page
#                            sizes, item limits, and part counts are
#                            all well under 1024.
#
# Phase 2 (--overflow-check) still probes the full integer range for
# under/overflow regardless of these bounds.
INT_BOUND = 1 << 30
SMALL_BOUND = 1 << 10

# --- Sentinels for value-domain modelling --------------------------
#
# ESBMC-Python has no `Optional`/`None` symbolic domain, so harnesses
# that must distinguish "absent" from "present but falsy" -- the exact
# distinction the Tier-2 findings turn on -- encode the domain as
# disjoint ints and assume membership. Keep NONE distinct from every
# legal value so `x is None` becomes `x == NONE`.
NONE = -(1 << 20)
