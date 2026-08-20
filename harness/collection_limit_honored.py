# SPDX-License-Identifier: Apache-2.0
# Positive control for candidate Finding A: the same two loops with
# the guard moved ahead of the item, i.e. the proposed fix.
#
# Proposed fix shape (both loops, collection.py:76-87 and :168-184):
#
#     for item in ...:
#         if limit is not None and count >= limit:
#             break            # test BEFORE consuming the item
#         ...
#         count += 1
#
# (An equally acceptable fix is to reject a non-positive `count` in
# `ResourceCollection.limit()` with a ValueError; this control models
# the loop-shape fix because it also repairs the `.limit(-n)` case
# without changing the public signature.)
#
# The control exists to show the Finding A harness is non-vacuous:
# identical loop structure and identical symbolic domain, corrected
# guard placement, Phase 1 + Phase 2 SUCCESSFUL. If this target ever
# regressed to FAILED, the counterexample in
# collection_limit_nonpositive.py would be evidence about the harness,
# not about boto3.
#
# Phase 1 + Phase 2 expected: SUCCESSFUL.

from stubs import nondet_int, __ESBMC_assume

NPAGES = 2


def collection_iter_count_fixed(page_len: int, limit: int) -> int:
    """As collection_limit_nonpositive.py, with the guard hoisted."""
    # --- fixed pages() ---------------------------------------------
    page_sizes = [0, 0]
    pages_yielded = 0
    count = 0
    p = 0
    while p < NPAGES:
        page_items = 0
        i = 0
        while i < page_len:
            if count >= limit:
                break
            page_items = page_items + 1
            count = count + 1
            i = i + 1
        page_sizes[pages_yielded] = page_items
        pages_yielded = pages_yielded + 1
        if count >= limit:
            break
        p = p + 1

    # --- fixed __iter__ --------------------------------------------
    delivered = 0
    q = 0
    while q < pages_yielded:
        k = 0
        while k < page_sizes[q]:
            if delivered >= limit:
                return delivered
            delivered = delivered + 1
            k = k + 1
        q = q + 1
    return delivered


def main() -> None:
    limit = nondet_int()
    __ESBMC_assume(-3 <= limit)
    __ESBMC_assume(limit <= 3)

    page_len = nondet_int()
    __ESBMC_assume(1 <= page_len)
    __ESBMC_assume(page_len <= 3)

    delivered = collection_iter_count_fixed(page_len, limit)

    assert delivered >= 0
    if limit <= 0:
        # The case the live defect gets wrong: nothing is delivered.
        assert delivered == 0
    else:
        assert delivered <= limit


main()
