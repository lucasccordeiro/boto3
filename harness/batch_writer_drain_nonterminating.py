# SPDX-License-Identifier: Apache-2.0
# Target: live-bug witness for BatchWriter.__exit__ failing to drain.
# Candidate Finding D, second consequence (see ROADMAP.md Tier 2).
#
# Sibling of batch_writer_flush_amount.py: same missing range check at
# table.py:99, a different observable failure.
#
# Trace against pinned boto/boto3 @ 1b554d2:
#
#   def __exit__(self, exc_type, exc_value, tb):     # table.py:163
#       # When we exit, we need to keep flushing whatever's left
#       # until there's nothing left in our items buffer.
#       while self._items_buffer:                    # :166
#           self._flush()                            # :167
#
# The loop has no other exit, so `_flush` must strictly shrink the
# buffer. It does not for flush_amount <= 0: `items_to_send =
# buffer[: flush_amount]` (:142) selects nothing once the buffer is
# down to `-flush_amount` entries, `buffer = buffer[flush_amount :]`
# (:143) puts every one of them back, and the loop re-enters with the
# same buffer it started with.
#
# Confirmed against the real class: with
# `Config(parameter_validation=False)` a three-item `put_item` run
# under `flush_amount=0` issued 8628 `BatchWriteItem` requests in five
# seconds, every one of them empty, and did not return -- an unbounded
# request flood from one `with` block. At `flush_amount=-1` the first
# two requests carry an item each and every one after that is empty,
# which is the same stall one step later. Under the default
# configuration botocore's min-length check on `RequestItems.<table>`
# turns the first empty request into a `ParamValidationError` instead,
# which is the failure batch_writer_flush_amount.py witnesses. Neither
# outcome is the caller's `flush_amount` being rejected.
#
# FAIRNESS. A retry loop over a service that never processes anything
# cannot terminate, and that is DynamoDB's behaviour rather than
# boto3's -- it is what `UnprocessedItems` and #483 / PR #562 are
# about. This harness therefore assumes the service processes at least
# one of the items it is *given*, so the non-termination below is
# attributable only to boto3 handing it none.
#
# batch_writer_flush_amount.py deliberately does not assume this: a
# table that returns everything unprocessed is what pushes the buffer
# past `flush_amount`, and that is the very case PR #562's slice was
# added to survive, so a size contract that assumed it away would
# prove nothing. The two harnesses differ on the assumption because
# they are asking different questions of the same code.
#
# Per constraint C5, unwinding assertions stay on. The property is
# nonetheless the loop variant rather than the unwinding bound: a
# variant violation says `_flush` made no progress at all, which is a
# non-termination proof independent of how large the bound is.
#
# Phase 1 expected: FAILED (the counterexample IS the bug report).
# Phase 2: skipped.

from stubs import nondet_int, __ESBMC_assume

DRAIN_BUFFERED_MAX = 3


def flush_batch_size(buffered: int, flush_amount: int) -> int:
    """len(self._items_buffer[: self._flush_amount]), table.py:142."""
    if flush_amount >= 0:
        if flush_amount < buffered:
            return flush_amount
        return buffered
    # Negative stop: buffer[:-k] keeps len - k entries, floored at zero.
    sent = buffered + flush_amount
    if sent < 0:
        return 0
    return sent


def drain(buffered: int, flush_amount: int) -> int:
    """`while self._items_buffer: self._flush()`, table.py:166-167."""
    steps = 0
    while buffered > 0:
        sent = flush_batch_size(buffered, flush_amount)

        unprocessed = nondet_int()
        __ESBMC_assume(0 <= unprocessed)
        __ESBMC_assume(unprocessed <= sent)
        if sent >= 1:
            __ESBMC_assume(unprocessed <= sent - 1)

        before = buffered
        # :143 keeps what was not sent; :153 appends UnprocessedItems.
        buffered = buffered - sent + unprocessed

        # The loop variant. `while self._items_buffer` has no other way
        # out, so a step that leaves the buffer no smaller is a step the
        # context manager will repeat forever.
        assert buffered < before

        steps = steps + 1
    return steps


def main() -> None:
    flush_amount = nondet_int()
    __ESBMC_assume(-2 <= flush_amount)
    __ESBMC_assume(flush_amount <= 2)

    # `__exit__` runs the drain loop exactly when something is left
    # buffered, which is the ordinary end of a `with` block.
    buffered = nondet_int()
    __ESBMC_assume(1 <= buffered)
    __ESBMC_assume(buffered <= DRAIN_BUFFERED_MAX)

    # The property is the variant inside the loop. A `steps <= buffered`
    # postcondition here would follow from it and could only ever fail
    # for a bound-dependent reason, which is what C5 says to avoid.
    drain(buffered, flush_amount)


main()
