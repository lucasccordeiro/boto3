# Finding D — ready to file at `boto/boto3`

**Status: not filed.** Paste the sections below into the fields of
<https://github.com/boto/boto3/issues/new?template=bug-report.yml>.

Verified against boto3 **1.34.46** / botocore 1.34.46 and against `develop` at
commit `11ef970` (1.43.85), where `BatchWriter.__init__`,
`_add_request_and_process`, `_flush_if_needed`, `_flush`, `__exit__` and
`TableResource.batch_writer` are all AST-identical to the tested release.
botocore's `range_check` on `develop` still reads `'min'` and never `'max'`.

**Prior art.** PR [#562](https://github.com/boto/boto3/pull/562) (merged 2016)
added the `[: self._flush_amount]` slice for exactly this invariant — "Ensure
batch writer never sends more than flush_amount", because a batch above the
API limit "will trigger an error". This report is the remaining way past that
guard: `flush_amount` itself is never checked. Issues
[#2188](https://github.com/boto/boto3/issues/2188) and PR
[#2196](https://github.com/boto/boto3/pull/2196) asked for `flush_amount` to be
exposed on `batch_writer()` and were closed unmerged, so the constructor is
still the only way to set it.

**Before filing:** replace *Environment details* with your own machine and
re-run the reproduction.

---

## Title

```
BatchWriter accepts any flush_amount, producing BatchWriteItem requests DynamoDB cannot accept
```

## Describe the bug

`BatchWriter.__init__` stores `flush_amount` with no range check
(`boto3/dynamodb/table.py:99`), and `_flush` builds the `BatchWriteItem`
request by slicing the buffer with it:

```python
def _flush_if_needed(self):
    if len(self._items_buffer) >= self._flush_amount:      # :138
        self._flush()

def _flush(self):
    items_to_send = self._items_buffer[: self._flush_amount]     # :142
    self._items_buffer = self._items_buffer[self._flush_amount :]
    response = self._client.batch_write_item(                    # :144
        RequestItems={self._table_name: items_to_send}
    )
```

DynamoDB bounds that list: the `BatchWriteItem` `RequestItems` value shape is
`{'min': 1, 'max': 25}`. Nothing in boto3 keeps `items_to_send` inside it, and
the two ends fail differently.

**`flush_amount <= 0`** makes `len(buffer) >= flush_amount` vacuously true, so
the first `put_item` flushes; `buffer[:0]` (or `buffer[:-k]` once the buffer is
down to `k` entries) selects nothing, and `buffer[0:]` puts everything back.
The request is empty and the buffer is unchanged. Under the default
configuration botocore rejects that request — with a message naming
`RequestItems.<table>`, which the caller never wrote. Under
`Config(parameter_validation=False)` nothing stops it, and `__exit__`'s
`while self._items_buffer: self._flush()` (`:166-167`) has no way out: it
issues empty `BatchWriteItem` requests forever.

**`flush_amount > 25`** builds an over-size batch. botocore's `range_check`
(`botocore/validate.py`) enforces only the *minimum* length — it reads
`'min'` from the shape metadata and never `'max'` — so the request is not
rejected locally and goes to DynamoDB, which rejects it.

## Regression Issue

No. Present since `BatchWriter` was introduced; unchanged on `develop` at
`11ef970`.

## Expected Behavior

`BatchWriter(table, client, flush_amount=n)` either accepts `n` and produces
requests DynamoDB can serve, or rejects `n` at the point it is passed, naming
`flush_amount`.

## Current Behavior

```
flush_amount=0, default configuration
  botocore.exceptions.ParamValidationError: Parameter validation failed:
  Invalid length for parameter RequestItems.repro, value: 0, valid min length: 1

flush_amount=0, Config(parameter_validation=False)
  500 BatchWriteItem requests issued, 500 of them empty, and still going;
  cut off by the reproducer, not by boto3

flush_amount=-1, Config(parameter_validation=False)
  500 BatchWriteItem requests issued, 498 of them empty, and still going

flush_amount=26  -> batch sizes [26]     (API maximum is 25)
flush_amount=30  -> batch sizes [30]
flush_amount=100 -> batch sizes [100]
```

The first case is the one most callers meet: the parameter they passed is
`flush_amount`, and the error names `RequestItems.repro`.

## Reproduction Steps

```python
import boto3
from boto3.dynamodb.table import BatchWriter

client = boto3.resource('dynamodb', region_name='us-east-1').meta.client

# 1. Rejected under a name the caller never used.
with BatchWriter('my-table', client, flush_amount=0) as batch:
    batch.put_item(Item={'id': 'a'})
# botocore.exceptions.ParamValidationError: Parameter validation failed:
# Invalid length for parameter RequestItems.my-table, value: 0,
# valid min length: 1

# 2. Above the maximum: one BatchWriteItem carrying 30 items goes out.
with BatchWriter('my-table', client, flush_amount=30) as batch:
    for i in range(30):
        batch.put_item(Item={'id': str(i)})
# The request body holds 30 items; nothing local rejects it.
```

For case 1's non-terminating form, build the resource with
`Config(parameter_validation=False)` and interrupt it — `__exit__` does not
return.

Case 2 was confirmed by capturing the serialized request with a `before-send`
handler, so what is established here is that boto3 *builds and issues* a
30-item `BatchWriteItem`. The service's rejection of it is not reproduced
against a live table in this report.

## Possible Solution

Range-check in `BatchWriter.__init__`, ahead of `:99`:

```python
if not 1 <= flush_amount <= 25:
    raise ValueError(
        f'flush_amount must be between 1 and 25, the BatchWriteItem '
        f'limit; got {flush_amount}'
    )
```

25 is DynamoDB's own bound, and PR #562 already relies on it. If a future API
version raises the limit, read it from
`client.meta.service_model.operation_model('BatchWriteItem')` rather than
hard-coding it.

## Additional Information/Context

- Affected code: `boto3/dynamodb/table.py:99` (`__init__`), `:137-139`
  (`_flush_if_needed`), `:141-146` (`_flush`), `:163-167` (`__exit__`).
- Reachable only through the `BatchWriter` constructor —
  `TableResource.batch_writer` (`:31-60`) does not forward `flush_amount`. The
  class docstring documents direct instantiation as supported (`:80-83`), and
  #2188 / #2196 show callers want to set it.
- The non-termination needs `Config(parameter_validation=False)`; with the
  default configuration the same call raises on the first `put_item` instead.
  Both come from the same missing check.
- botocore cannot cover the upper bound: `range_check` in
  `botocore/validate.py` reads `'min'` from the shape and never `'max'`, on
  `develop` as well as on the tested release.
- Scope note: the over-size request is shown leaving boto3 with 30 items in it;
  the service's response to it was not exercised against a live table.
- Found with [ESBMC](https://github.com/esbmc/esbmc) bounded model checking.
  Two properties, two counterexamples: at `flush_amount = 0` the batch boto3
  builds falls outside `1 <= len <= 25`, and at `flush_amount = -2` `_flush`
  leaves the buffer no smaller than it found it, so `__exit__`'s loop has no
  variant.

## SDK version used

```
boto3 1.34.46 / botocore 1.34.46; code path unchanged on develop at 11ef970 (1.43.85).
```

## Environment details (OS name and version, etc.)

```
Ubuntu 24.04.4 LTS, x86_64, CPython 3.12.3
```
