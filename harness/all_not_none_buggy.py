# SPDX-License-Identifier: Apache-2.0
# Target: boto3.resources.response.all_not_none (buggy control).
#
# Mirrors all_not_none.py but replaces the absence test
#     if element is None:
# with the truthiness test upstream explicitly warns against
#     if not element:
# which in the int domain rejects 0 as well as the absent sentinel.
#
# This is the mutation that turns the helper into the defect shape
# found live elsewhere in boto3 (`if num:`, `if val and ...`,
# `if search_response:`). ESBMC's counterexample is an iterable whose
# only "invalid" element is the legal value 0.
#
# Expected verdicts:
#   Phase 1 (default flags):    FAILED   (completeness direction fails)
#   Phase 2 (--overflow-check): skipped  (Phase 1 already FAILED)

from stubs import nondet_int, __ESBMC_assume, NONE

N = 4


def all_not_none_truthy(xs: list[int], n: int) -> bool:
    """`if not element` in place of `if element is None`."""
    i = 0
    while i < n:
        if xs[i] == 0 or xs[i] == NONE:
            return False
        i = i + 1
    return True


def main() -> None:
    xs = [0, 0, 0, 0]

    i = 0
    while i < N:
        v = nondet_int()
        __ESBMC_assume(v == NONE or (0 <= v and v <= 3))
        xs[i] = v
        i = i + 1

    result = all_not_none_truthy(xs, N)

    any_none = False
    k = 0
    while k < N:
        if xs[k] == NONE:
            any_none = True
        k = k + 1

    assert result == (not any_none)


main()
