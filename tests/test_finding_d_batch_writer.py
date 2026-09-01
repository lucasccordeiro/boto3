# SPDX-License-Identifier: Apache-2.0
"""Finding D, driven by ESBMC's counterexamples.

`harness/batch_writer_flush_amount.py` and
`harness/batch_writer_drain_nonterminating.py` both fail Phase 1;
their counterexamples are recorded under `generated/` and applied here
to the real `BatchWriter`.

  boto3/dynamodb/table.py:31-60    TableResource.batch_writer
  boto3/dynamodb/table.py:66-100   BatchWriter.__init__
  boto3/dynamodb/table.py:137-158  _flush_if_needed / _flush
  boto3/dynamodb/table.py:163-167  __exit__
"""

import inspect
import json

import pytest
from botocore.config import Config
from botocore.exceptions import ParamValidationError

from boto3.dynamodb.table import BatchWriter, TableResource

from aws_capture import capturing_resource
from esbmc_witness import witness_values

# DynamoDB BatchWriteItem: the RequestItems value shape is
# {'type': 'list', 'min': 1, 'max': 25}.
BATCH_MIN = 1
BATCH_MAX = 25

TABLE = "witness"
BATCH_WRITE_OK = b'{"UnprocessedItems": {}}'

FLUSH_AMOUNT, BUFFERED = witness_values(
    "batch_writer_flush_amount", "flush_amount", "buffered"
)
DRAIN_FLUSH_AMOUNT, DRAIN_BUFFERED = witness_values(
    "batch_writer_drain_nonterminating", "flush_amount", "buffered"
)

# Enough requests that no terminating run could reach it.
FLOOD_CAP = 200


class _Flood(Exception):
    """Raised to cut short a run that would not otherwise stop."""


class _CappedRequests(list):
    """Records each captured request, and stops a run that will not."""

    def __init__(self, stop_after):
        super().__init__()
        self._stop_after = stop_after

    def append(self, body):
        super().append(body)
        if len(self) >= self._stop_after:
            raise _Flood()

    def batch_sizes(self):
        return [len(json.loads(b)["RequestItems"][TABLE]) for b in self]


def _run(flush_amount, nitems, validate=True):
    """Write `nitems` items; return (batch sizes, hit the request cap).

    Every run is capped: a flush amount from either witness makes
    `__exit__` non-terminating, so an uncapped run would hang the
    suite rather than fail it.
    """
    sent = _CappedRequests(FLOOD_CAP)
    resource = capturing_resource(
        "dynamodb",
        BATCH_WRITE_OK,
        sent,
        config=None if validate else Config(parameter_validation=False),
    )
    try:
        with BatchWriter(
            TABLE, resource.meta.client, flush_amount=flush_amount
        ) as writer:
            for i in range(nitems):
                writer.put_item(Item={"id": f"k{i}"})
    except _Flood:
        return sent.batch_sizes(), True
    return sent.batch_sizes(), False


def _batch_sizes(flush_amount, nitems, validate=True):
    """`_run` for a flush amount expected to terminate."""
    sizes, capped = _run(flush_amount, nitems, validate)
    assert not capped, f"flush_amount={flush_amount} did not terminate"
    return sizes


def test_witnesses_are_the_defect_case():
    """Guard: regenerated counterexamples must still exercise the bug."""
    # Both bounds of the harness's contract are violable, so a
    # regenerated counterexample could land above BATCH_MAX instead --
    # where no flush fires during the puts and the tests below have
    # nothing to observe. They need the empty-batch end specifically.
    assert FLUSH_AMOUNT < BATCH_MIN, (
        f"ESBMC witness flush_amount={FLUSH_AMOUNT} is not the empty-batch case"
    )
    assert BUFFERED >= 1, f"ESBMC witness buffered={BUFFERED} flushes nothing"
    assert DRAIN_FLUSH_AMOUNT <= 0, (
        f"ESBMC witness flush_amount={DRAIN_FLUSH_AMOUNT} leaves the drain "
        f"loop a variant"
    )
    assert DRAIN_BUFFERED >= 1


def test_flush_amount_is_stored_without_validation():
    """table.py:99 -- the whole finding in one line."""
    resource = capturing_resource("dynamodb", BATCH_WRITE_OK, [])
    writer = BatchWriter(TABLE, resource.meta.client, flush_amount=FLUSH_AMOUNT)
    assert writer._flush_amount == FLUSH_AMOUNT


def test_witness_flush_amount_builds_an_empty_batch():
    """The bug. Fails once upstream range-checks flush_amount."""
    sizes, _ = _run(FLUSH_AMOUNT, BUFFERED, validate=False)

    assert sizes, "expected at least one BatchWriteItem request"
    assert min(sizes) < BATCH_MIN, (
        f"flush_amount={FLUSH_AMOUNT} produced batch sizes {sizes}; "
        f"BatchWriteItem requires at least {BATCH_MIN} item"
    )


def test_witness_flush_amount_error_names_a_parameter_the_caller_never_wrote():
    """Under the default configuration botocore rejects boto3's request."""
    with pytest.raises(ParamValidationError) as excinfo:
        _run(FLUSH_AMOUNT, BUFFERED)

    message = str(excinfo.value)
    assert f"RequestItems.{TABLE}" in message
    assert "min length: 1" in message
    assert "flush_amount" not in message


def test_drain_witness_never_returns():
    """`__exit__`'s loop has no variant for a non-positive flush amount."""
    sizes, capped = _run(DRAIN_FLUSH_AMOUNT, DRAIN_BUFFERED, validate=False)

    assert capped, "expected the drain loop to still be running at the cap"
    # Once the buffer is down to the entries the slice cannot reach,
    # every further request is empty.
    assert set(sizes[-10:]) == {0}


def test_flush_amount_above_the_maximum_builds_an_oversize_batch():
    """The witness harness's second assertion, `sent <= 25`.

    ESBMC reports one counterexample per run and picked the
    lower-bound one; `--multi-property` shows both violable. Only
    `BATCH_MAX + 1` is inside the harness's symbolic domain
    (`flush_amount <= BATCH_MAX + 2`) -- the larger values are here to
    show the same slice scales, and are the reproducer's.
    """
    for n in (BATCH_MAX + 1, 30, 100):
        assert _batch_sizes(n, n) == [n]


def test_legal_flush_amounts_stay_within_the_api_bounds():
    """Control: the defect is invisible for every flush amount in range."""
    for n, items in ((BATCH_MAX, 60), (1, 3), (10, 25)):
        sizes = _batch_sizes(n, items)
        assert sizes and all(BATCH_MIN <= s <= BATCH_MAX for s in sizes)


def test_fix_rejecting_the_flush_amount_never_builds_the_batch():
    """The proposed fix, on the same witness inputs."""

    def checked(flush_amount):
        if not BATCH_MIN <= flush_amount <= BATCH_MAX:
            raise ValueError(
                f"flush_amount must be between {BATCH_MIN} and {BATCH_MAX}, "
                f"the BatchWriteItem limit; got {flush_amount}"
            )
        return flush_amount

    for rejected in (FLUSH_AMOUNT, DRAIN_FLUSH_AMOUNT, 0, BATCH_MAX + 1):
        with pytest.raises(ValueError, match="flush_amount"):
            checked(rejected)
    for accepted in (1, 25):
        assert checked(accepted) == accepted


def test_reachability_flush_amount_is_constructor_only():
    """Pins the scope claim: `batch_writer()` does not forward it."""
    assert "flush_amount" not in inspect.signature(
        TableResource.batch_writer
    ).parameters
    assert "flush_amount" in inspect.signature(BatchWriter.__init__).parameters
    docstring = " ".join(BatchWriter.__init__.__doc__.split())
    assert "instantiate this class directly" in docstring
