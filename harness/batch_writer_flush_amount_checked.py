# SPDX-License-Identifier: Apache-2.0
# Positive control for candidate Finding D: the same two models with
# `flush_amount` range-checked, i.e. the proposed fix.
#
# Proposed fix shape (table.py:96, ahead of the assignment at :99):
#
#     if not 1 <= flush_amount <= 25:
#         raise ValueError(
#             'flush_amount must be between 1 and 25, '
#             'the BatchWriteItem limit'
#         )
#
# The bound is DynamoDB's, not a choice: the BatchWriteItem
# `RequestItems` value shape is {'min': 1, 'max': 25}, and PR #562
# already added the `[: flush_amount]` slice at :142 to keep
# unprocessed items from pushing a batch past it. Checking
# `flush_amount` closes the remaining way in.
#
# One control for both witnesses, because both are consequences of the
# one missing check: with the guard in place every batch boto3 issues
# is a legal BatchWriteItem *and* `__exit__`'s drain loop has a
# variant. If this target ever regressed to FAILED, the counterexamples
# in batch_writer_flush_amount.py and
# batch_writer_drain_nonterminating.py would be evidence about the
# harness, not about boto3.
#
# Phase 1 + Phase 2 expected: SUCCESSFUL.

from stubs import nondet_int, __ESBMC_assume

BATCH_MIN = 1
BATCH_MAX = 25

DRAIN_BUFFERED_MAX = 3


def flush_batch_size(buffered: int, flush_amount: int) -> int:
    """len(self._items_buffer[: self._flush_amount]), table.py:142."""
    if flush_amount >= 0:
        if flush_amount < buffered:
            return flush_amount
        return buffered
    sent = buffered + flush_amount
    if sent < 0:
        return 0
    return sent


def drain(buffered: int, flush_amount: int) -> int:
    """As batch_writer_drain_nonterminating.py, unchanged."""
    steps = 0
    while buffered > 0:
        sent = flush_batch_size(buffered, flush_amount)

        unprocessed = nondet_int()
        __ESBMC_assume(0 <= unprocessed)
        __ESBMC_assume(unprocessed <= sent)
        if sent >= 1:
            __ESBMC_assume(unprocessed <= sent - 1)

        before = buffered
        buffered = buffered - sent + unprocessed
        assert buffered < before

        steps = steps + 1
    return steps


def main() -> None:
    flush_amount = nondet_int()
    __ESBMC_assume(BATCH_MIN <= flush_amount)
    __ESBMC_assume(flush_amount <= BATCH_MAX)

    # Same precondition as the witness: `buffered >= 1`, with no upper
    # tie to `flush_amount`. That matters here -- it is what makes this
    # a proof that the `[: flush_amount]` slice PR #562 added clamps
    # *any* buffer depth, including the deep ones UnprocessedItems
    # creates, back into the legal range.
    buffered = nondet_int()
    __ESBMC_assume(1 <= buffered)
    __ESBMC_assume(buffered <= BATCH_MAX + 2)

    sent = flush_batch_size(buffered, flush_amount)
    assert sent >= BATCH_MIN
    assert sent <= BATCH_MAX

    drained = nondet_int()
    __ESBMC_assume(1 <= drained)
    __ESBMC_assume(drained <= DRAIN_BUFFERED_MAX)

    # Terminates: the variant inside `drain` holds at every step, and
    # the unwinding assertion confirms the loop fits the bound.
    drain(drained, flush_amount)


main()
