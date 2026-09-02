#!/usr/bin/env python3
"""
Empirical reproducer — candidate Finding D
  boto3/dynamodb/table.py:66-100   BatchWriter.__init__
  boto3/dynamodb/table.py:137-158  _flush_if_needed / _flush
  boto3/dynamodb/table.py:163-167  __exit__

  `BatchWriter(..., flush_amount=n)` stores `n` with no range check.
  DynamoDB's BatchWriteItem accepts between 1 and 25 items per table;
  boto3 slices the buffer by `flush_amount` to build that list, so a
  value outside the range produces a request the service cannot accept
  — and, for `n <= 0`, a `__exit__` that never returns.

Source (verbatim, boto/boto3 @ 1b554d2 — the four methods below are
AST-identical between the pin, the installed release, and current
develop):

  def __init__(self, table_name, client, flush_amount=25, ...):
      ...
      self._flush_amount = flush_amount            # :99   no check

  def _flush_if_needed(self):
      if len(self._items_buffer) >= self._flush_amount:   # :138
          self._flush()

  def _flush(self):
      items_to_send = self._items_buffer[: self._flush_amount]   # :142
      self._items_buffer = self._items_buffer[self._flush_amount :]
      response = self._client.batch_write_item(                  # :144
          RequestItems={self._table_name: items_to_send}
      )

  def __exit__(self, exc_type, exc_value, tb):
      while self._items_buffer:                    # :166
          self._flush()                            # :167

Three observable failures, all from that one missing check:

  flush_amount <= 0   items_to_send is empty. botocore's ParamValidator
                      rejects it — but with a message naming
                      `RequestItems.<table>`, a parameter the caller
                      never wrote.

  flush_amount <= 0, and validation disabled with
  `Config(parameter_validation=False)`
                      nothing stops the empty request. `_flush` selects
                      nothing and puts the whole buffer back, so
                      `__exit__` re-enters with the buffer it started
                      with and issues empty BatchWriteItem calls
                      forever.

  flush_amount > 25   items_to_send holds `flush_amount` entries.
                      botocore checks only the *minimum* length
                      (`botocore/validate.py` `range_check` reads
                      'min', never 'max'), so the over-size request is
                      issued. What this script establishes is that
                      boto3 builds and sends it; the service's
                      response is not exercised.

Reachability: `TableResource.batch_writer` does not forward
`flush_amount` (:31-60), so this needs the `BatchWriter` constructor —
which the class docstring documents as a supported entry point (:80-83,
"if you're going to instantiate this class directly"). Exposing
`flush_amount` on `batch_writer()` has been asked for twice (#2188, PR
#2196, closed unmerged).

This script drives the REAL `BatchWriter` against a real DynamoDB
resource. No request leaves the machine: a `before-send` handler
captures the serialized request and returns a canned success, so the
batch sizes printed are the ones boto3 actually put on the wire.

Dependencies: boto3 (any version whose dynamodb/table.py matches the
pin — see the version note at the bottom).
"""

import json

import boto3
from botocore.awsrequest import AWSResponse
from botocore.config import Config
from botocore.exceptions import ParamValidationError

from boto3.dynamodb.table import BatchWriter

TABLE = 'repro'
BATCH_WRITE_OK = b'{"UnprocessedItems": {}}'

# DynamoDB BatchWriteItem: the RequestItems value shape is
# {'type': 'list', 'min': 1, 'max': 25}.
BATCH_MIN = 1
BATCH_MAX = 25


class _CannedBody:
    def __init__(self, payload):
        self._payload = payload

    def stream(self, **kwargs):
        return iter([self._payload])


class _Flood(Exception):
    """Raised to cut short a run that would not otherwise stop."""


def capturing_client(batch_sizes, validate=True, stop_after=None):
    """A real DynamoDB client whose requests are captured, not sent."""

    def before_send(request, **kwargs):
        body = request.body
        payload = body.decode() if isinstance(body, bytes) else body
        batch_sizes.append(len(json.loads(payload)['RequestItems'][TABLE]))
        if stop_after is not None and len(batch_sizes) >= stop_after:
            raise _Flood()
        return AWSResponse(request.url, 200, {}, _CannedBody(BATCH_WRITE_OK))

    resource = boto3.resource(
        'dynamodb',
        region_name='us-east-1',
        aws_access_key_id='dummy',
        aws_secret_access_key='dummy',
        config=None if validate else Config(parameter_validation=False),
    )
    resource.meta.client.meta.events.register(
        'before-send.dynamodb', before_send
    )
    return resource.meta.client


def run(flush_amount, nitems, batch_sizes, validate=True, stop_after=None):
    """Write `nitems` items through a BatchWriter, recording batch sizes."""
    client = capturing_client(batch_sizes, validate, stop_after)
    with BatchWriter(TABLE, client, flush_amount=flush_amount) as writer:
        for i in range(nitems):
            writer.put_item(Item={'id': f'k{i}'})


# ── Step 1: the default batch size behaves as documented ─────────────

print(f'boto3 {boto3.__version__}')
print()
print('Legal flush amounts (correct):')
for n, items in ((25, 60), (1, 3), (10, 25)):
    sizes = []
    run(n, items, sizes)
    assert all(BATCH_MIN <= s <= BATCH_MAX for s in sizes), sizes
    print(f'  flush_amount={n:>3}, {items:>2} items -> batch sizes {sizes}')

# ── Step 2: flush_amount <= 0 — an empty batch, under the caller's name ──

print()
print('Non-positive flush amounts (the defect):')
for n in (0, -1, -5):
    try:
        run(n, 3, [])
    except ParamValidationError as exc:
        first = str(exc).strip().splitlines()[-1].strip()
        print(f'  flush_amount={n:>3} -> {type(exc).__name__}: {first}')
        assert 'RequestItems' in first and 'min length: 1' in first
    else:  # pragma: no cover - the defect is that this does not happen
        raise AssertionError(f'flush_amount={n} did not raise')

print()
print(
    '  -> the caller passed flush_amount; the error names '
    'RequestItems.<table>.'
)

# ── Step 3: with validation off, __exit__ never returns ──────────────

FLOOD_CAP = 500

print()
print('The same call with Config(parameter_validation=False):')
for n in (0, -1):
    sizes = []
    try:
        run(n, 3, sizes, validate=False, stop_after=FLOOD_CAP)
    except _Flood:
        pass
    assert len(sizes) >= FLOOD_CAP, sizes
    # Once the buffer is down to the `-flush_amount` entries the slice
    # cannot reach, every further request is empty.
    assert set(sizes[-10:]) == {0}, sorted(set(sizes[-10:]))
    empty = sizes.count(0)
    print(
        f'  flush_amount={n:>3} -> {len(sizes)} requests issued, '
        f'{empty} of them empty, and still going; cut off by the '
        f'reproducer, not by boto3'
    )

print()
print(
    '  -> `while self._items_buffer: self._flush()` has no variant: '
    '_flush\n     selects nothing and puts the whole buffer back.'
)

# ── Step 4: flush_amount > 25 — an over-size batch, unchecked ────────

print()
print('Flush amounts above the BatchWriteItem maximum:')
for n in (26, 30, 100):
    sizes = []
    run(n, n, sizes)
    assert sizes == [n], sizes
    print(
        f'  flush_amount={n:>3} -> batch sizes {sizes} '
        f'(API maximum is {BATCH_MAX}); botocore checks only the minimum'
    )

# ── Step 5: contrast — what the fix would produce ────────────────────


def checked_flush_amount(flush_amount):
    """The proposed fix: range-check in BatchWriter.__init__."""
    if not BATCH_MIN <= flush_amount <= BATCH_MAX:
        raise ValueError(
            f'flush_amount must be between {BATCH_MIN} and {BATCH_MAX}, '
            f'the BatchWriteItem limit; got {flush_amount}'
        )
    return flush_amount


print()
print('With flush_amount range-checked in __init__ (proposed fix):')
for n in (0, -1, 26, 100, 1, 25):
    try:
        checked_flush_amount(n)
    except ValueError as exc:
        print(f'  flush_amount={n:>3} -> ValueError: {exc}')
    else:
        print(f'  flush_amount={n:>3} -> accepted')

print()
print(
    'Reproduced: flush_amount <= 0 builds an empty batch and a drain '
    'loop with no\nvariant; flush_amount > 25 builds a batch past the '
    'documented API limit.'
)

# Version note: this was run against the installed boto3 above. The
# bodies of BatchWriter.__init__, _add_request_and_process,
# _flush_if_needed, _flush, __exit__ and TableResource.batch_writer are
# AST-identical between that release, the pin (1b554d2, boto3 1.43.75)
# and current develop (11ef970, 1.43.85), so the observation carries.
# To re-check against another checkout, with $PIN pointing at a boto3
# working tree:
#
#   python3 - "$PIN/boto3/dynamodb/table.py" <<'EOF'
#   import ast, sys
#   import boto3.dynamodb.table as installed
#
#   def body(src, cls, name):
#       for n in ast.walk(ast.parse(src)):
#           if isinstance(n, ast.ClassDef) and n.name == cls:
#               for f in n.body:
#                   if isinstance(f, ast.FunctionDef) and f.name == name:
#                       b = f.body
#                       if isinstance(b[0], ast.Expr) and isinstance(
#                           b[0].value, ast.Constant
#                       ):
#                           b = b[1:]
#                       return ast.dump(ast.Module(body=b, type_ignores=[]))
#       raise LookupError(f'{cls}.{name}')
#
#   here = open(installed.__file__).read()
#   there = open(sys.argv[1]).read()
#   for cls, name in (
#       ('BatchWriter', '__init__'),
#       ('BatchWriter', '_add_request_and_process'),
#       ('BatchWriter', '_flush_if_needed'),
#       ('BatchWriter', '_flush'),
#       ('BatchWriter', '__exit__'),
#       ('TableResource', 'batch_writer'),
#   ):
#       same = body(here, cls, name) == body(there, cls, name)
#       print(f'{cls}.{name}: {"identical" if same else "DIFFERS"}')
#   EOF
