# SPDX-License-Identifier: Apache-2.0
# Target: live-bug witness for BatchWriter(flush_amount) outside
# [1, 25]. Candidate Finding D (see ROADMAP.md Tier 2).
#
# Trace against pinned boto/boto3 @ 1b554d2:
#
#   1. `BatchWriter.__init__` (table.py:66-100) stores the value
#      verbatim at :99 -- `self._flush_amount = flush_amount`. There is
#      no range check anywhere on the path.
#
#   2. `_flush_if_needed` (table.py:137-139) fires on
#          if len(self._items_buffer) >= self._flush_amount:
#      which is vacuously true for every flush_amount <= 0.
#
#   3. `_flush` (table.py:141-146) builds the request by slicing:
#          items_to_send = self._items_buffer[: self._flush_amount]
#          self._items_buffer = self._items_buffer[self._flush_amount :]
#          self._client.batch_write_item(
#              RequestItems={self._table_name: items_to_send})
#
# DynamoDB's BatchWriteItem contract bounds that list: the
# `RequestItems` value shape carries {'min': 1, 'max': 25}. boto3 never
# ensures `items_to_send` respects it, and the two ends fail
# differently:
#
#   flush_amount <= 0  ->  items_to_send is EMPTY. botocore's
#       ParamValidator range-checks list length, but only the lower
#       bound (validate.py `range_check` reads 'min', never 'max'), so
#       the caller gets
#           ParamValidationError: Invalid length for parameter
#           RequestItems.<table>, value: 0, valid min length: 1
#       naming a parameter they never constructed. Under
#       `Config(parameter_validation=False)` nothing stops it and the
#       empty request goes on the wire -- see
#       batch_writer_drain_nonterminating.py for what happens then.
#
#   flush_amount > 25  ->  items_to_send holds flush_amount entries.
#       botocore does NOT check the maximum, so the over-size request
#       is issued. Confirmed against the real class: flush_amount=30
#       with 30 put_item calls builds and sends one 30-item
#       BatchWriteItem. That it exceeds the service's documented limit
#       is the API reference and the shape metadata; the service's
#       response to it is not exercised here.
#
# Both ends are one missing range check at :99. PR #562 ("Ensure batch
# writer never sends more than flush_amount", merged 2016) already
# closed the *other* route past the batch maximum -- unprocessed items
# pushing the buffer beyond `flush_amount` -- so the invariant asserted
# below is one upstream has already accepted; what is missing is a
# bound on `flush_amount` itself.
#
# REACHABILITY. `TableResource.batch_writer` (table.py:31-60) does not
# forward `flush_amount`, so this is programmatic-only: it needs the
# `BatchWriter` constructor, which the class docstring documents as a
# supported entry point (:80-83, "if you're going to instantiate this
# class directly"). Exposing `flush_amount` on `batch_writer()` has
# been asked for twice (#2188, PR #2196, closed unmerged), so a caller
# who wants a different batch size reaches for the constructor by
# design.
#
# Harness shape: which items get sent depends only on the buffer's
# length, never on its contents, so a buffer is modelled by its length
# and the slice arithmetic -- including Python's negative-stop
# semantics -- is reproduced exactly.
#
# `_flush` has two call sites and between them they place no upper
# bound on the buffer depth: `_flush_if_needed` (:138) fires at
# `flush_amount`, but `_flush` then appends `UnprocessedItems` back
# (:153) *after* the slice without re-testing, so a table returning
# items unprocessed leaves the buffer deeper than `flush_amount` --
# measured depths 3, 4, 5, 6 at successive flushes with
# flush_amount=3 and everything echoed back. `__exit__` (:166) calls
# `_flush` at any depth >= 1. So `buffered >= 1` is the precondition,
# and it is the only one.
#
# Phase 1 expected: FAILED (the counterexample IS the bug report).
# Phase 2: skipped.

from stubs import nondet_int, __ESBMC_assume

# DynamoDB BatchWriteItem: the RequestItems value shape is
# {'type': 'list', 'min': 1, 'max': 25}.
BATCH_MIN = 1
BATCH_MAX = 25


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


def main() -> None:
    flush_amount = nondet_int()
    __ESBMC_assume(-2 <= flush_amount)
    __ESBMC_assume(flush_amount <= BATCH_MAX + 2)

    buffered = nondet_int()
    __ESBMC_assume(1 <= buffered)
    __ESBMC_assume(buffered <= BATCH_MAX + 2)

    sent = flush_batch_size(buffered, flush_amount)

    # DynamoDB's BatchWriteItem contract: every batch boto3 puts on the
    # wire carries between 1 and 25 items. The lower bound is violated
    # at every flush_amount <= 0, the upper at every flush_amount > 25.
    assert sent >= BATCH_MIN
    assert sent <= BATCH_MAX


main()
