# boto3 ESBMC-Python verification PoC

Applying [ESBMC](https://github.com/esbmc/esbmc)'s Python frontend to
[boto/boto3](https://github.com/boto/boto3), the AWS SDK for Python.

Modelled on the [AWS-Neuron](https://github.com/lucasccordeiro/AWS-Neuron),
[vLLM](https://github.com/lucasccordeiro/vllm) and
[chromium-dashboard](https://github.com/lucasccordeiro/chromium-dashboard-esbmc)
PoCs — same two-phase driver, same buggy-control discipline, same layout.

## Status

**Planning stage.** [`ROADMAP.md`](./ROADMAP.md) is the verification plan: a
five-tier target list of ~27 rows with per-row source citations, properties,
buggy mutations, expected verdicts, and a recommended sequence.

**11 verification targets** are implemented as the seed that proves the
toolchain and the finding pipeline end to end. `make verify` (two phases per
target) completes in ~2 min with 0 failures.

**Four live findings, all confirmed empirically** against the real boto3 code,
each with an executable pytest reproduction generated from ESBMC's
counterexample (see [below](#from-counterexample-to-executable-test)).
Finding F is filed upstream as
**[boto/boto3#4831](https://github.com/boto/boto3/issues/4831)**; the other three drafts are written and unfiled.

### Finding A — `ResourceCollection.limit(0)` returns one resource

`ResourceCollection.limit(0)` — and `.limit(-n)` for any n — issues a real
service request and returns **exactly
one resource**, where the documented contract (`collection.py:244`) is "Return
no more than this many items".

`ResourceCollection.limit(count)` (`boto3/resources/collection.py:231-247`)
stores `count` verbatim with no positivity check, and both iteration loops
consume an item *before* comparing against the limit:

```python
# collection.py:168-184  ResourceCollection.pages
for item in self._handler(self._parent, params, page):
    page_items.append(item)                     # item already kept
    count += 1
    if limit is not None and count >= limit:    # 1 >= 0 -> fires, too late
        break

# collection.py:76-87  ResourceCollection.__iter__
for item in page:
    yield item                                  # item already emitted
    count += 1
    if limit is not None and count >= limit:
        return
```

The off-by-one is invisible for every positive limit, which is why it survives
the test suite.

**Reachability.** `pages()` branches on `client.can_paginate` at
`collection.py:145-164`, and the two branches behave differently:

| Collection | `.limit(0)` | `.limit(-n)` |
|---|---|---|
| Not paginatable — `ec2.key_pairs`, `ec2.placement_groups`, `ec2.classic_addresses`, `ec2.vpc_addresses`, `Instance.vpc_addresses`, `cloudwatch Metric.alarms`, `iam.saml_providers` (7 of 88 on boto3 1.43.76) | **1 resource** | **1 resource** |
| Paginatable — `bucket.objects`, the other 81 | 0, masked | **1 resource** while `n < page_len` |

That set drifts: `s3.buckets` was on the first row until botocore added a
`ListBuckets` paginator, which silently moved it to the second. The tests assert
the precondition rather than assume it.

On the paginatable branch the limit also reaches botocore as
`PaginationConfig={'MaxItems': limit}`, and `_truncate_response` keeps
`original[:max_items]`. At `0` that slice is empty and the defect is hidden; at
a small negative limit the slice is *negative*, dropping the last `n` items
rather than truncating to zero, so a page still arrives and the loops yield one
resource from it. `tests/test_finding_a_reachability.py` pins both branches
against real service resources.

`harness/collection_limit_nonpositive.py` is the ESBMC witness
(counterexample: `limit = 0`, `delivered = 1`);
`reproducer/finding_a_collection_limit_nonpositive.py` reproduces it against
the real `ResourceCollection` class; `harness/collection_limit_honored.py`
verifies the proposed fix (SUCCESSFUL, both phases). The issue draft in
[`bug-reports/`](./bug-reports/) is **not filed** — it supersedes
[boto/boto3#4670](https://github.com/boto/boto3/issues/4670), which reported this
in Dec 2025 and was closed as irreproducible after the reporter tested a
paginatable collection. See ROADMAP.md "Upstream filing policy".

### Finding D — `BatchWriter` accepts any `flush_amount`

`BatchWriter.__init__` stores `flush_amount` with no range check
(`dynamodb/table.py:99`), and `_flush` builds the `BatchWriteItem` request by
slicing the buffer with it (`:142`). DynamoDB bounds that list —
`RequestItems`' value shape is `{'min': 1, 'max': 25}` — and boto3 keeps
`items_to_send` inside it for no value of `flush_amount` a caller might pass:

| `flush_amount` | What the caller sees |
|---|---|
| `<= 0`, default config | `ParamValidationError: Invalid length for parameter RequestItems.<table>, value: 0, valid min length: 1` on the **first** `put_item`. The word `flush_amount` does not appear. |
| `<= 0`, `Config(parameter_validation=False)` | `__exit__` never returns. At `flush_amount=0`: 8628 `BatchWriteItem` requests in five seconds from a three-item `with` block, every one empty, still going. |
| `> 25` | boto3 builds and sends an over-size batch. botocore's `range_check` reads `'min'` and never `'max'`, so nothing local stops it. Measured: `flush_amount=30` produces one 30-item request. |

The non-termination is `while self._items_buffer: self._flush()` (`:166-167`)
with no other exit: for `flush_amount <= 0`, `_flush` selects nothing
(`buffer[:0]`, or `buffer[:-k]` once the buffer is down to `k` entries) and
puts the whole buffer back, so the loop re-enters with what it started with.

PR [#562](https://github.com/boto/boto3/pull/562) (merged 2016) added the
`[: flush_amount]` slice for exactly this invariant — "Ensure batch writer
never sends more than flush_amount" — so it is one upstream already accepts.
What is missing is a bound on `flush_amount` itself.

**Reachability.** `TableResource.batch_writer` (`:31-60`) does not forward
`flush_amount`, so this needs the `BatchWriter` constructor — which the class
docstring documents as a supported entry point (`:80-83`). Exposing
`flush_amount` on `batch_writer()` has been asked for twice
([#2188](https://github.com/boto/boto3/issues/2188), PR
[#2196](https://github.com/boto/boto3/pull/2196), closed unmerged).

`harness/batch_writer_flush_amount.py` is the ESBMC witness for the batch-size
contract (counterexample `flush_amount = 0`, `sent = 0`) and
`harness/batch_writer_drain_nonterminating.py` for the drain loop's missing
variant (counterexample `flush_amount = -2`, buffer stuck at 2);
`reproducer/finding_d_batch_writer_flush_amount.py` reproduces all three
failures against the real `BatchWriter`;
`harness/batch_writer_flush_amount_checked.py` verifies the range-check fix
(SUCCESSFUL, both phases). The issue draft in [`bug-reports/`](./bug-reports/)
is **not filed**.

### Finding F — DynamoDB placeholders overwrite the caller's

**Reachability.** The caller does not have to invent `#n0`: boto3's own public
`ConditionExpressionBuilder` produces it, and feeding its output back into
`update_item` alongside an `Attr(...)` condition is enough. The combination is
also ordinary — `update_item` has no `Attr`-based builder for
`UpdateExpression`, so a conditional update must mix a raw expression with a
generated condition.

boto3 generates DynamoDB placeholder names starting at `#n0` / `:v0` on every
request (`conditions.py:313-322`, reset at `transform.py:172`), then merges
them **over** the caller's own with `dict.update`
(`transform.py:203-213`). A call that combines a raw expression carrying the
caller's `ExpressionAttributeNames` with a `ConditionExpression` built from
`Attr(...)` therefore sends a request naming an attribute the caller never
supplied — no error, wrong data:

```python
table.update_item(
    Key={'id': 'x'},
    UpdateExpression='SET #n0 = :v0',
    ExpressionAttributeNames={'#n0': 'colour'},
    ExpressionAttributeValues={':v0': 'red'},
    ConditionExpression=Attr('locked').eq(False),
)
# request sent: ExpressionAttributeNames  {'#n0': 'locked'}
#               ExpressionAttributeValues {':v0': {'BOOL': False}}
```

The caller asked to set `colour = 'red'`; the request sets `locked = false`.
Reads corrupt identically — a `query` filtering `score > 10` is sent filtering
`id > 'x'`. The caller's own dicts are deep-copied first
(`transform.py:35-36`), so nothing in the caller's process shows it.

`harness/dynamodb_placeholder_merge.py` is the ESBMC witness (counterexample:
caller and generator both bind `#n0`);
`reproducer/finding_f_dynamodb_placeholder_collision.py` captures the
serialized request from the real DynamoDB resource and also demonstrates the
fix against the real objects; `harness/dynamodb_placeholder_merge_fixed.py`
verifies the skip-on-collision fix (SUCCESSFUL, both phases).

**Filed upstream as [boto/boto3#4831](https://github.com/boto/boto3/issues/4831)** (2026-08-20, open). It supersedes
[#4032](https://github.com/boto/boto3/issues/4032), which reported the same
defect from the values side in Feb 2024 and was closed with a workaround.

### Finding G — `create_tags` raises after the request is sent

The EC2 `Tag` shape has no required members, so
`ec2.create_tags(Tags=[{'Key': 'env'}])` passes botocore validation and the
`CreateTags` request goes on the wire. `ec2/createtags.py:38` then dereferences
`tag['Value']` unguarded:

```python
self.meta.client.create_tags(**kwargs)          # :27  request sent
...
        tag_resource = self.Tag(
            resource, tag['Key'], tag['Value']  # :38  KeyError: 'Value'
        )
```

The caller gets a raw `KeyError` for an operation that has already been issued,
cannot tell whether the tags were applied, and re-tags if it retries.
`harness/create_tags_missing_value.py` is the ESBMC witness — the property is
"no exception escapes once the request at `:27` has been issued";
`reproducer/finding_g_create_tags_missing_value.py` captures the request body
at the moment of the raise. One reachability question is left open in the issue
draft: whether EC2 stores a tag submitted without a `Value` (empty tag values
are legal, so the expectation is yes) — confirm on a live account before
filing.

### ESBMC-Python frontend defects, found and filed

Two, both filed upstream.

A parameter annotated with a bare `list` loses its elements' type in the callee:
arithmetic on `param[i]` raises a spurious `TypeError`, and `param[i] == 5`
silently evaluates false with no exception at all. Annotating `list[int]` fixes
both — [esbmc#7187](https://github.com/esbmc/esbmc/issues/7187), five-case
reproducer in
[`reproducer/esbmc_list_parameter_type_tag_loss.py`](./reproducer/esbmc_list_parameter_type_tag_loss.py).

`--generate-pytest-testcase` emits a file that cannot run: it opens with
`from <module> import *`, which re-executes the program under CPython where the
nondet intrinsics do not exist, and calls the entry function with the nondet
values as positional arguments regardless of its real signature —
[esbmc#7188](https://github.com/esbmc/esbmc/issues/7188).

These are constraints **C1** and **C6** in ROADMAP.md.

## Quickstart

```
make verify                                   # both phases, every target
make phase1                                   # functional contracts only
make phase2                                   # --overflow-check only
make verify-only T=collection_limit_nonpositive   # one target
make testgen                                  # regenerate pytest witnesses
make test                                     # run the tests against real boto3

# With a non-PATH ESBMC binary:
make verify ESBMC=/path/to/esbmc
```

Requires ESBMC ≥ 8.4.0 built with the Python frontend, and `pytest` for
`make test`. Verdicts below hold on 8.4.0 and 8.5.0.

## From counterexample to executable test

Each live-bug witness is turned into a running test against the real boto3 in
three steps, so the reproduction is tied to the verifier's output rather than
to a hand transcription of it:

1. `make testgen` runs each witness under
   [`--generate-pytest-testcase`](https://esbmc.github.io/docs/python/pytest-testgen/),
   which writes the counterexample inputs to `tests/generated/test_<harness>.py`
   as a `@pytest.mark.parametrize` list.
2. `tests/esbmc_witness.py` parses those values out.
3. `tests/test_finding_*.py` applies them to the real boto3 API and asserts the
   observed behaviour.

The generated files are **parsed, never imported**. They open with
`from <harness> import *`, and a harness is deliberately not importable under
CPython: `harness/stubs.py` declares `nondet_int` and friends only under
`TYPE_CHECKING`, because a runtime definition would shadow the ESBMC intrinsic,
collapse every symbolic value to a constant, and let the whole suite pass
vacuously. Making the generated files directly runnable would mean breaking
that rule, so they are treated as data.

Values ESBMC picked, and what each drives:

| Witness | Counterexample | Drives |
|---|---|---|
| `collection_limit_nonpositive` | `limit=0, page_len=2` | `.limit(0)` on a real `ResourceCollection` |
| `batch_writer_flush_amount` | `flush_amount=0, buffered=12` | `BatchWriter` slicing a real buffer to build `BatchWriteItem` |
| `batch_writer_drain_nonterminating` | `flush_amount=-2, buffered=3` | `__exit__` on a real `BatchWriter`, capped by the test |
| `create_tags_missing_value` | tags `(Key,Value)` = present/present, present/present, present/**absent**; `n_resources=2` | `ec2.create_tags` with a `Value`-less tag |
| `dynamodb_placeholder_merge` | caller binds `#n0`; `gen_count=3` | `update_item` mixing a raw expression with `ConditionExpression` |

Each finding gets a bug test and a fix test **on the same witness inputs**, so
the pair is a differential check rather than an assertion about one run. Every
bug test was confirmed to fail when the corresponding fix is applied to the
real boto3 code.

## Layout

```
harness/
  stubs.py                          # nondet intrinsics + shared bounds/sentinels
  all_not_none.py                   # resources/response.py:20         (SUCCESSFUL)
  all_not_none_buggy.py             #   `not x` for `x is None`        (FAILED)
  collection_limit_nonpositive.py   # Finding A witness                (FAILED, LIVE BUG)
  collection_limit_honored.py       #   positive control: the fix      (SUCCESSFUL)
  batch_writer_flush_amount.py      # Finding D witness                (FAILED, LIVE BUG)
  batch_writer_drain_nonterminating.py #  Finding D, non-termination   (FAILED, LIVE BUG)
  batch_writer_flush_amount_checked.py #  positive control: the fix    (SUCCESSFUL)
  dynamodb_placeholder_merge.py     # Finding F witness                (FAILED, LIVE BUG)
  dynamodb_placeholder_merge_fixed.py #  positive control: the fix     (SUCCESSFUL)
  create_tags_missing_value.py      # Finding G witness                (FAILED, LIVE BUG)
  create_tags_missing_value_fixed.py #  positive control: the fix      (SUCCESSFUL)
tests/
  esbmc_witness.py                  # parses counterexamples out of generated/
  aws_capture.py                    # real boto3 client, request captured not sent
  generated/                        # ESBMC --generate-pytest-testcase output
  test_finding_a_collection_limit.py
  test_finding_d_batch_writer.py
  test_finding_f_dynamodb_placeholders.py
  test_finding_g_create_tags.py
verify.py                           # manifest + two-phase driver + --testgen
Makefile                            # verify / phase1 / phase2 / verify-only / testgen / test
ROADMAP.md                          # the verification plan: five tiers, ~27 rows,
                                    # modelling constraints, sequence, scope
bug-reports/                        # upstream issue text (F filed as #4831)
reproducer/                         # standalone CPython reproducers
```

## Two-phase verification

| Phase | Flags | Catches |
|---|---|---|
| 1 | (default) | Functional contracts via `assert`: bounds, index safety, dispatch totality, accounting identities. |
| 2 | `--overflow-check` | CWE-190 (signed overflow), CWE-369 (division by zero) on host integer math. |

A buggy target — or a live-bug witness — whose Phase 1 already fails skips
Phase 2.

`verify.py` additionally fails any SUCCESSFUL verdict that generated **0
VCCs**: symbolic execution that never reached a user assertion is passing
vacuously. This guard exists because that failure mode is otherwise silent.

## Targets

| Target | Entry | Phase 1 VCCs | Phase 2 VCCs |
|---|---|---|---|
| `all_not_none` | `all_not_none.py` | 463 ✓ | 477 ✓ |
| `all_not_none_buggy` | `all_not_none_buggy.py` | 571 ✗ | — |
| `collection_limit_nonpositive` | `collection_limit_nonpositive.py` | **335 ✗ (Finding A)** | — |
| `collection_limit_honored` | `collection_limit_honored.py` | 337 ✓ | 404 ✓ |
| `batch_writer_flush_amount` | `batch_writer_flush_amount.py` | **2 ✗ (Finding D)** | — |
| `batch_writer_drain_nonterminating` | `batch_writer_drain_nonterminating.py` | **6 ✗ (Finding D)** | — |
| `batch_writer_flush_amount_checked` | `batch_writer_flush_amount_checked.py` | 8 ✓ | 35 ✓ |
| `create_tags_missing_value` | `create_tags_missing_value.py` | **932 ✗ (Finding G)** | — |
| `create_tags_missing_value_fixed` | `create_tags_missing_value_fixed.py` | 357 ✓ | 389 ✓ |
| `dynamodb_placeholder_merge` | `dynamodb_placeholder_merge.py` | **1232 ✗ (Finding F)** | — |
| `dynamodb_placeholder_merge_fixed` | `dynamodb_placeholder_merge_fixed.py` | 2152 ✓ | 2201 ✓ |

✓ = SUCCESSFUL (expected), ✗ = FAILED (expected).

## Provenance

- **ESBMC**: https://github.com/esbmc/esbmc — versions 8.4.0 and 8.5.0,
  default Bitwuzla solver.
- **boto3**: https://github.com/boto/boto3 — pinned at commit `1b554d2`
  (version 1.43.75, 2026-08-20).
