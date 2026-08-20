# SPDX-License-Identifier: Apache-2.0
# Target: boto3.resources.response.all_not_none (non-buggy).
#
# Source (boto/boto3 @ 1b554d2):
#     boto3/resources/response.py:20
#     def all_not_none(iterable):
#         """
#         Return True if all elements of the iterable are not None (or if the
#         iterable is empty). This is like the built-in ``all``, except checks
#         against None, so 0 and False are allowable values.
#         """
#         for element in iterable:
#             if element is None:
#                 return False
#         return True
#
# Why this is Tier 1 row 1: `all_not_none` is boto3's own statement of
# the invariant that the Tier-2 findings violate elsewhere -- absence
# (`is None`) is not the same test as falsiness (`not x`), and 0 /
# False / '' / [] are legal values that must survive the guard. The
# contract proved here is the reference the buggy control (and the
# Tier-2 witnesses) are measured against.
#
# Modelling notes:
#   1. ESBMC-Python has no symbolic `None`, so the element domain is
#      encoded as disjoint ints with `NONE` (stubs.py) standing in for
#      the absent value. `element is None` becomes `element == NONE`.
#      The legal domain deliberately includes 0, the falsy-but-valid
#      value the docstring guarantees.
#   2. The predicate is inlined into `main` rather than written as
#      `all_not_none(xs, n)`: under ESBMC 8.4.0 a list passed as a
#      function argument loses its elements' type tags in the callee,
#      so `xs[i] == NONE` there is modelled as an uncaught TypeError
#      and the harness fails spuriously. See
#      ../reproducer/esbmc_list_parameter_type_tag_loss.py.
#
# Phase 1: the biconditional contract -- the result is True exactly
#          when no element is the absent sentinel.
# Phase 2: --overflow-check on the loop counter arithmetic.

from stubs import nondet_int, __ESBMC_assume, NONE

N = 4


def main() -> None:
    xs = [0, 0, 0, 0]

    # Each element is either the absent sentinel or a legal value.
    # 0 is in the legal domain on purpose: it is what the buggy
    # control rejects.
    i = 0
    while i < N:
        v = nondet_int()
        __ESBMC_assume(v == NONE or (0 <= v and v <= 3))
        xs[i] = v
        i = i + 1

    # --- all_not_none, response.py:26-29 ---------------------------
    result = True
    j = 0
    while j < N:
        if xs[j] == NONE:
            result = False
            break
        j = j + 1

    # Independent recomputation of the reference property: a full scan
    # with no early exit, so the two disagree if the loop above stops
    # on the wrong condition.
    any_none = False
    k = 0
    while k < N:
        if xs[k] == NONE:
            any_none = True
        k = k + 1

    # Postcondition: the predicate is exactly "no element is absent".
    # Both directions matter -- soundness (never True with an absent
    # element) and completeness (never False without one, which is
    # where the truthiness mutation fails).
    assert result == (not any_none)


main()
