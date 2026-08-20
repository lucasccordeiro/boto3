# boto3 / ESBMC-Python PoC — verification roadmap

Forward plan for applying [ESBMC](https://github.com/esbmc/esbmc)'s Python
frontend to [boto/boto3](https://github.com/boto/boto3), the AWS SDK for
Python. Modelled on the
[AWS-Neuron](https://github.com/lucasccordeiro/AWS-Neuron),
[vLLM](https://github.com/lucasccordeiro/vllm) and
[chromium-dashboard](https://github.com/lucasccordeiro/chromium-dashboard-esbmc)
PoCs: same two-phase driver, same buggy-control discipline, same
harness/verify.py/Makefile layout, same "the counterexample is the bug
report" convention.

Pinned at `boto/boto3` commit `1b554d2` (boto3 1.43.75, 2026-08-20).

---

## Context and approach

boto3 is a thin, *very* widely deployed layer: 8,558 lines of Python across
`boto3/` (excluding `boto3/docs/`), sitting on top of botocore and
s3transfer. That shape is what makes it a good ESBMC-Python target and also
dictates where the verification effort goes.

**What boto3 actually contains.** Almost no numerical kernels — the arithmetic
that exists is index and counter arithmetic in four places: collection
pagination, the reverse-JMESPath parameter builder, the DynamoDB batch writer,
and S3 transfer configuration. What boto3 *does* contain in quantity is
**parameter plumbing**: values the user hands to a public API that are stored
verbatim, carried across several modules, and only dereferenced much later —
often after a service call has already been made. That is precisely the defect
class the three prior PoCs found repeatedly, under three recurring shapes:

| Shape | Prior instances | boto3 analogue |
|---|---|---|
| **Silent acceptance** — an out-of-domain value is stored, no error, wrong behaviour later | vLLM `--max-logprobs <neg>`, `--long-prefill-token-threshold <neg>`; chromium `?num=0`, `?mstone=0` | `.limit(0)`, `TransferConfig(multipart_chunksize=0)`, `preferred_transfer_client='crtt'` |
| **Falsy-vs-absent guard** — `if x:` / `if x and …` where `if x is not None:` was meant | chromium Findings D and F | `if search_response:`, `if self._overwrite_by_pkeys:` |
| **Bare exception at the boundary** — user input reaches an unguarded index or attribute, so the caller gets a `KeyError`/`AttributeError` instead of a clean error | chromium Findings A, G, H, I, J; vLLM bare `AssertionError` | `tag['Value']`, `value.pop(0)`, `search_response[i]` |

boto3 adds a fourth shape the earlier targets did not have, and it is the most
interesting one here:

| Shape | boto3 instance |
|---|---|
| **Silent state corruption after a successful call** — the request succeeds, then boto3 mangles the result or the caller's own state | generated DynamoDB placeholders overwriting the caller's `ExpressionAttributeNames`; `create_tags` raising after the tags are created; `TransferConfig` mutated in place by a download |

**Consequence for tiering.** Tier 1 is deliberately small — there is not much
pure arithmetic to prove, and proving it is not where the value is. The weight
is in Tiers 2–4, which hunt live defects on the public API surface, and Tier 5,
which turns the DynamoDB condition-expression builder into a proof of absence
for expression injection (the boto3 analogue of chromium's gate-approval
security invariants).

---

## Method

Unchanged from the prior PoCs; restated so this document stands alone.

1. **Two phases per target.** Phase 1 runs default flags and checks functional
   contracts written as `assert`. Phase 2 adds `--overflow-check` for CWE-190
   (signed overflow) and CWE-369 (division by zero) on the host integer math.
   A target whose Phase 1 already FAILS skips Phase 2.
2. **Every non-buggy target has a buggy control**, and every live-bug witness
   has a **positive control** modelling the proposed fix. A witness whose
   paired control does not verify is evidence about the harness, not about
   boto3, and does not get filed.
3. **The vacuity guard is not optional.** `verify.py` fails any SUCCESSFUL
   verdict that generated 0 VCCs. This exists because the failure mode is
   silent: in the vLLM PoC a runtime `def nondet_int()` shadowed the ESBMC
   intrinsic, every assertion was sliced away, and the suite reported green.
4. **Preconditions are `__ESBMC_assume`, postconditions are `assert`.** Stub
   only what the target reads. Do not invent behaviour a caller does not rely
   on — an over-specified stub turns a real counterexample into a harness
   artefact.
5b. **The counterexample drives the reproduction.** Live-bug witnesses are
   marked `testcase=True` in `verify.py` and emitted as pytest witnesses by
   `make testgen`
   ([`--generate-pytest-testcase`](https://esbmc.github.io/docs/python/pytest-testgen/)).
   `tests/` parses the values out and applies them to the real boto3 API, so
   the executable reproduction cannot drift from what the verifier actually
   found. The generated files are parsed, never imported — a harness is not
   importable under CPython by design (see C6).
5. **No finding is filed on an ESBMC verdict alone.** A counterexample is a
   hypothesis about the source; it becomes a finding only after a standalone
   CPython reproducer drives the *real* boto3 code and shows the behaviour.
   All three confirmed findings — A, F and G — have both.
6. **Pin everything.** Harness headers quote the upstream source verbatim with
   `file:line` against commit `1b554d2`. When a reproducer runs against a
   different installed release, the header records an AST comparison of the
   functions under test between that release and the pin.

---

## Modelling constraints (ESBMC-Python)

Measured against **ESBMC 8.4.0** on this repository's harnesses. These are
verifier limits, not boto3 facts; they shape what a harness may look like.

**C1 — lists must not cross a function boundary.** A list passed as an
argument loses its elements' type tags in the callee. `param[i] == 5` there is
modelled as an uncaught `TypeError`, and arithmetic on `param[i]` likewise, so
the harness fails spuriously while the same code inlined into `main` verifies.
Minimal reproducer and the exact four cases (two failing, two passing):
[`reproducer/esbmc_list_parameter_type_tag_loss.py`](./reproducer/esbmc_list_parameter_type_tag_loss.py).
**Workaround:** create and consume each list inside one function — inline the
loop into `main` (`harness/all_not_none.py`) or keep the list local to a
helper (`harness/collection_limit_nonpositive.py`, where `page_sizes` never
leaves `collection_iter_count`). **To do:** file upstream against
`esbmc/esbmc`; the prior PoCs filed four such frontend issues
(esbmc#4756, #4909, #4926, #5022) and three were fixed, so this is worth the
report.

**C2 — no symbolic `None`.** Every boto3 target in Tiers 2–4 turns on the
difference between "absent" and "present but falsy", which is exactly what
cannot be expressed directly. Encode the domain as disjoint ints with the
`NONE` sentinel from `harness/stubs.py`, and translate `x is None` to
`x == NONE`, `not x` to `x == 0 or x == NONE`. Keep the legal domain
containing 0 — that value is the whole point of the exercise.

**C3 — no strings, dicts, or objects worth relying on.** boto3's data flow is
dicts of strings; ESBMC-Python is comfortable with ints and function-local
lists. Model dict lookups as parallel arrays or as a "key present" boolean,
and model a string as its salient property (length, or a small tag for
"matches / does not match"). Where the target's property genuinely depends on
string content (`ATTR_NAME_REGEX` in Tier 5), verify the *arithmetic*
consequence — placeholder counts and uniqueness — rather than the regex.

**C4 — expected bounds.** `INT_BOUND = 1 << 30` for linear properties;
`SMALL_BOUND = 1 << 10` where the postcondition is non-linear in the symbolic
inputs. Every realistic boto3 quantity (page sizes, item limits, part counts,
batch sizes) is far below 1024, so `SMALL_BOUND` is not a real restriction on
coverage — say so in the harness header rather than leaving it implied.

**C6 — generated pytest files are data, not tests.** ESBMC writes
`from <harness> import *` at the top of every generated file and derives the
call signature from the nondet assignments, so
`test_main(limit, page_len)` calls a `main()` that takes no arguments. Neither
is fixable without making harnesses runtime-importable, and that is forbidden:
`harness/stubs.py` keeps the nondet intrinsics under `TYPE_CHECKING` precisely
so no runtime definition can shadow them and collapse the suite into a vacuous
pass. **Workaround:** parse the `@pytest.mark.parametrize` list
(`tests/esbmc_witness.py`) and apply the values to the real API by hand. **To
do:** worth raising upstream — a `--pytest-values-only` emitting just the
witness dict would remove the parsing step.

**C5 — `--unwind`, never `--no-unwinding-assertions`.** Two planned targets
(Tier 2 row 4, Tier 3 row 3) prove **non-termination** by showing a loop
cannot fit inside its bound. That argument is only sound with unwinding
assertions on; disabling them produces false SUCCESSFUL on truncated loops.
If a full unwind is infeasible, switch to `--k-induction` and require
convergence — do not lower the bound.

---

## Already covered

Eight targets, `make verify` green in ~2 min, plus 24 pytest reproductions
(`make test`) driven by ESBMC's own counterexamples. This is the seed that proves the
toolchain, the driver, and the finding pipeline end to end — not a claim of
coverage.

| Family | Targets | Status |
|---|---|---|
| Tier 1 — pure predicates | `all_not_none`, `all_not_none_buggy` | Phase 1 + 2 SUCCESSFUL / FAILED as expected. Establishes the `is None` vs `not x` contract that Tiers 2–4 violate elsewhere. |
| Tier 2 — silent acceptance | `collection_limit_nonpositive` (witness), `collection_limit_honored` (positive control) | Witness Phase 1 FAILED at `limit = 0`, `delivered = 1`. Control SUCCESSFUL both phases. **Finding A confirmed empirically** against the real `ResourceCollection`. |
| Tier 4 — data integrity | `dynamodb_placeholder_merge` (witness), `dynamodb_placeholder_merge_fixed` (positive control) | Witness Phase 1 FAILED with the caller and the generator both binding `#n0`. Control SUCCESSFUL both phases. **Finding F confirmed empirically** — the captured request writes an attribute the caller never named. |
| Tier 3 — bare exceptions | `create_tags_missing_value` (witness), `create_tags_missing_value_fixed` (positive control) | Witness Phase 1 FAILED with a tag carrying `Key` and no `Value`. Control SUCCESSFUL both phases. **Finding G confirmed** — the request is issued, then boto3 raises `KeyError`. |
| Test generation | `tests/` + `tests/generated/` | `make testgen` runs each live-bug witness under `--generate-pytest-testcase`; the emitted counterexample drives the real boto3 API in `tests/test_finding_*.py`. Each finding has a bug test and a fix test on the same inputs, and every bug test was confirmed to fail when the fix is applied to real boto3. |
| Frontend | — | ESBMC-Python constraint C1 found and reduced while writing `all_not_none`; reproducer committed. |

---

## Tier 1 — Pure predicates and helpers

Small by design. These exist to pin the contracts the rest of the plan
measures against, and to give the suite non-vacuous SUCCESSFUL verdicts.

| # | Target | Source | Property | Buggy mutation |
|---|---|---|---|---|
| 1 | `all_not_none` ✅ | `resources/response.py:20` | Result is True exactly when no element is absent; 0 and False are admitted | `if not element` instead of `if element is None` |
| 2 | `resolve_init_args` | `s3/transfer.py:393-402` | Every key of `init_args` appears in `resolved`; a non-`None` value passes through verbatim; `None` becomes the default or `UNSET_DEFAULT` | Truthiness test (`if val:`) — silently rewrites a user's `max_concurrency=0` to the default 10 |
| 3 | `has_minimum_crt_version` | `s3/transfer.py:230-242` | Tuple ordering agrees with component-wise version comparison for well-formed versions; a malformed version yields False, never an exception | Lexicographic string compare — `"0.9.0" >= "0.19.18"` |
| 4 | `build_param_structure_fill` | `resources/params.py:142-155` | After the fill loop, `len(pos[part]) > index`, so the write at `:155` is in bounds; the list grows by exactly `index + 1 - len` entries | `<` instead of `<=` in the `while` at `:149` → IndexError at the boundary |
| 5 | `build_param_structure_name` | `resources/params.py:134-135` | `part[: -len(f"{index}[]")]` strips exactly the `[<digits>]` suffix — i.e. `len(str(int(s))) == len(s)` for the matched index text | — (this row is a **probe**, see Tier 3 row 5: the identity fails for `[01]`, `[+1]`, `[ 1]`) |

Rows 2 and 3 double as positive controls: they are places boto3 gets the
absent-vs-falsy distinction *right*, which is what makes the Tier-2 rows
credible as defects rather than as house style.

---

## Tier 2 — Silent acceptance of unvalidated public parameters

The live-bug hunt. Each row: a value a caller can pass through a documented
public API, which no guard rejects, and which produces wrong behaviour rather
than an error. Expected Phase-1 verdict is **FAILED** — the counterexample is
the witness. Each gets a paired positive control modelling the fix.

### Finding A — `ResourceCollection.limit(n)`, n ≤ 0 ✅ CONFIRMED

`collection.py:231-247` stores `count` with no validation; both iteration
loops (`:76-87` and `:168-184`) consume an item and only then compare against
the limit, so `.limit(0)` issues a real service request and yields exactly one
resource. Documented contract at `:244` is "Return no more than this many
items". Invisible for every positive limit.

- Witness: `harness/collection_limit_nonpositive.py` — FAILED at `limit = 0`.
- Control: `harness/collection_limit_honored.py` — SUCCESSFUL, guard hoisted.
- Reproducer: `reproducer/finding_a_collection_limit_nonpositive.py`, driving
  the real `ResourceCollection`.
- Issue draft: `bug-reports/finding-a-collection-limit-nonpositive.md`
  (**unfiled**).

### Finding B (candidate) — `TransferConfig` numeric fields unvalidated

`s3/transfer.py:272-361`. None of `multipart_threshold`, `max_concurrency`,
`multipart_chunksize`, `num_download_attempts`, `max_io_queue`, `io_chunksize`
or `max_bandwidth` is range-checked; `_resolve_init_args` only distinguishes
`None` from not-`None`. A zero `multipart_chunksize` reaches s3transfer's part
arithmetic; a zero `max_concurrency` reaches `ThreadPoolExecutor`. The failure
therefore surfaces from a dependency, with a message that does not name the
boto3 parameter that caused it.

Harness: model the resolve-then-consume chain over a symbolic field value;
assert the implicit precondition (`chunksize >= 1`) that the downstream
division requires. Phase 2 with `--overflow-check` is the natural home for the
division-by-zero claim. **Reachability caveat to settle before filing:** decide
per field whether the eventual exception is clear enough to count as a defect —
the honest classification for some of these may be "confusing error", not
"silent acceptance", the way vLLM Finding #7 turned out not to be a live bug.

### Finding C (candidate) — `preferred_transfer_client` unknown value

`s3/transfer.py:193-227` lowercases the string and tests it against the three
constants in `s3/constants.py:16-18`. An unrecognised value — `'crtt'`,
`'CRT '`, `'default'` — matches nothing and falls through to
`return False` at `:227`: the classic transfer manager, silently. A user who
typoes `preferred_transfer_client` gets the opposite of what they configured
with no diagnostic.

The asymmetry is the argument: `crt.py:176-197` *does* validate, raising
`InvalidCrtTransferConfigError` for options incompatible with the CRT client —
but only after `:182` establishes `preferred_transfer_client == 'crt'`. So the
codebase validates the *combination* and not the *value*.

Harness: enumerate the value domain as {classic, crt, auto, other} and assert
that the resolved client is a documented function of the request; the `other`
case violates it. Positive control: the `crt.py:185-191` loop, which is
exhaustive over `DEFAULTS` and does reject.

### Finding D (candidate) — `BatchWriter(flush_amount <= 0)` does not terminate

`dynamodb/table.py:137-167`. With `flush_amount = 0`, `_flush_if_needed`'s
`len(buffer) >= 0` is always true, and `_flush` slices `buffer[:0]` /
`buffer[0:]` — it sends an empty batch and **drains nothing**. With a negative
flush amount, `buffer[:-k]` / `buffer[-k:]` retains the tail forever. Either
way `__exit__`'s `while self._items_buffer: self._flush()` (`:165-167`) cannot
make progress.

`flush_amount` is not exposed through `TableResource.batch_writer`
(`table.py:31-60`), so this is programmatic-only — reachable through the
constructor the class docstring explicitly documents as a supported entry point
(`table.py:80-83`). vLLM Finding #8 was programmatic-only in exactly this sense
and was fixed upstream anyway.

Harness: the vLLM `hash_block_size_negative_propagation` shape — bound the
loop with `--unwind` and let the unwinding assertion witness non-termination.
See constraint **C5**: unwinding assertions stay on.

### Finding E (candidate) — `page_size(n)` for n ≤ 0

`collection.py:249-260` stores the value; `:154` forwards it verbatim as
`PaginationConfig={'MaxItems': limit, 'PageSize': page_size}`. Adjacent to
Finding A and likely bundled with it in one issue. The Finding A reproducer
already prints the `PaginationConfig` dict boto3 hands to botocore, which shows
`MaxItems: 0` and `MaxItems: -5` going over the wire — evidence for the bundle.

### Summary table — Tier 2

| # | Source | Admitted value | Effect | Fix shape |
|---|---|---|---|---|
| A ✅ | `collection.py:231` | `limit(0)`, `limit(-n)` | One resource returned after a real request; contract says none | Test the limit before consuming the item, in both loops |
| B | `s3/transfer.py:272` | `multipart_chunksize=0`, `max_concurrency=0`, … | Exception from s3transfer/`ThreadPoolExecutor` naming neither boto3 nor the parameter | Range-check in `TransferConfig.__init__` |
| C | `s3/transfer.py:197` | `preferred_transfer_client='crtt'` | Silently uses the classic client | Reject values outside `s3/constants.py` |
| D | `dynamodb/table.py:99` | `BatchWriter(flush_amount=0)` | `__exit__` cannot drain the buffer — non-terminating | `if flush_amount < 1: raise ValueError` |
| E | `collection.py:249` | `page_size(0)`, `page_size(-n)` | `PageSize: 0` forwarded to botocore | Same validation as A |

---

## Tier 3 — Bare exceptions at the API boundary

The chromium Findings G/H/I/J class, transplanted: user input reaches an
unguarded index, key, or attribute, and the caller gets a raw
`KeyError`/`IndexError`/`AttributeError` instead of a clean error — in two of
these rows, *after* the service call has already succeeded.

| # | Target | Source | Trigger | Why it matters |
|---|---|---|---|---|
| 1 ✅ | `create_tags` missing `Value` | `ec2/createtags.py:38` | `Tags=[{'Key': 'k'}]` | **Finding G, confirmed** — see below. |
| 2 | `_extract_pkey_values` | `dynamodb/table.py:124-135` | `overwrite_by_pkeys` naming an attribute absent from a request | `request['PutRequest']['Item'][key]` (`:127`) and `['DeleteRequest']['Key'][key]` (`:132`) are unguarded. A delete's `Key` carries only the primary key, so any `overwrite_by_pkeys` entry that is not part of the key raises `KeyError` from `delete_item` while `put_item` on the same table works. |
| 3 | `_remove_dup_pkeys_request_if_any` | `dynamodb/table.py:114-122` | duplicate buffered items | `self._items_buffer.remove(item)` mutates the list being iterated at `:116-118`. Today the dedup invariant means at most one match exists, so the skip is benign — verify **that invariant**, and pair it with a buggy control that breaks it, rather than reporting the loop. Honest outcome here is probably a proof of absence. |
| 4 | `ResourceHandler` plural path | `resources/response.py:240-253`, `:306-307` | a response whose search path yields fewer items than the identifier list | Two distinct index hazards: `search_response[i]` at `:253` is indexed over `range(len(plural[0]))` (`:247`) with no length agreement, and `handle_response_item`'s `value.pop(0)` at `:307` pops from *every* list identifier, so identifiers of unequal length raise `IndexError: pop from empty list`. Also `:252` is `if search_response:` — a falsy guard, so an empty search result silently produces resources with no data. Model-driven rather than user-driven, so expect a **latent-precondition** classification like vLLM's `get_num_blocks`. |
| 5 | `build_param_structure` index text | `resources/params.py:134-135` | a resource-model target such as `foo[01]`, `foo[+1]`, `foo[-1]` | `part[: -len(f"{index}[]")]` assumes the index's text length equals `len(str(int(text)))`. For `[01]` it truncates one character too few and corrupts the parameter name; for `[-1]` the fill loop at `:149` does not run and `pos[part][-1] = value` raises `IndexError` on the empty list. Driven by botocore's bundled resource JSON, not by user input — verify the precondition and state the reachability honestly. |
| 6 | `TypeDeserializer._deserialize_n` | `dynamodb/types.py:37-42`, `:288-289` | an item written by another SDK with > 38 significant digits | `DYNAMODB_CONTEXT` traps `Inexact` and `Rounded`, so `create_decimal` raises `decimal.Inexact` out of `deserialize` — a read path failing on data DynamoDB itself accepted. Long-standing and likely known; **search the boto3 issue tracker before writing the harness** and, if it is already filed, keep it as a verification target and drop it from the findings list. |
| 7 | `_build_name_placeholder` `%`-format | `dynamodb/conditions.py:428-437` | `Attr('a[1%]')`, `Attr('')` | `placeholder_format % tuple(args)` at `:437` re-interprets any `%` that the regex did not capture — bracket interiors — as a format spec, raising `ValueError`. `Attr('')` produces an empty fragment and a malformed expression. Lowest severity in the tier; include for completeness, file only if a realistic attribute name reaches it. |

### Finding G — `create_tags` raises after the request is sent ✅ CONFIRMED

The EC2 `Tag` shape has **no required members**
(`shape_for('Tag').required_members == []`), so `Tags=[{'Key': 'env'}]` passes
botocore validation and the `CreateTags` request goes on the wire. Only then
does `ec2/createtags.py:38` dereference the tag:

```python
self.meta.client.create_tags(**kwargs)          # :27  request sent
...
        tag_resource = self.Tag(
            resource, tag['Key'], tag['Value']  # :38  KeyError: 'Value'
        )
```

**Confirmed empirically** — captured from a `before-send` handler at the moment
of the raise:

```
raised: KeyError('Value')
requests already sent: 1
body: Action=CreateTags&Version=2016-11-15&ResourceId.1=i-0123...&Tag.1.Key=env
```

`Tags=[{'Value': 'prod'}]` raises `KeyError('Key')` from the same line. The
ordering is the point: whatever validation this method needs belongs before
`:27`, not after it. The caller cannot tell whether the tags were applied, and
a retry re-tags.

**Reachability question still open.** The reproducer uses a canned response, so
it establishes boto3's ordering — request issued, then raise — but not that EC2
*accepts* a tag without a `Value`. Empty tag values are legal in EC2, so the
expectation is that it does; confirm against a live account before filing. If
EC2 rejects the call, the finding survives as an unguarded dereference raising
a raw `KeyError`, but the "succeeded, then raised" framing must be dropped.

| Artefact | Path |
|---|---|
| Witness | `harness/create_tags_missing_value.py` — Phase 1 FAILED, counterexample is a tag with `Key` and no `Value` |
| Positive control | `harness/create_tags_missing_value_fixed.py` — `.get('Value', '')`, both phases SUCCESSFUL |
| Reproducer | `reproducer/finding_g_create_tags_missing_value.py` — five cases incl. the fix on the real resource |
| Issue draft | `bug-reports/finding-g-create-tags-missing-value.md` — **unfiled** |

Fix shape: `tag.get('Key')` / `tag.get('Value', '')` — the empty string is what
EC2 stores for an omitted value, so this keeps a call the API accepts working.
Validating ahead of `:27` is the alternative, but it rejects a request EC2
handles.

---

## Tier 4 — State and data-integrity invariants (flagship)

Where a request succeeds and boto3 then corrupts the result or the caller's
own state. This is the tier with the highest severity ceiling and the one with
no counterpart in the earlier PoCs.

### Finding F — generated placeholders overwrite the caller's ✅ CONFIRMED

`ConditionExpressionBuilder` restarts at `#n0` / `:v0` on every request
(`conditions.py:313-322`, reset unconditionally at `transform.py:172`), and the
merge at `transform.py:203-213` lets the generated mapping win:

```python
if expr_attr_names_input in params:
    params[expr_attr_names_input].update(generated_names)
```

`dict.update` overwrites on key collision. A request that combines a *raw*
expression carrying the caller's own `ExpressionAttributeNames`/`Values` with a
`ConditionExpression` built from `Attr(...)` therefore has the caller's `#n0`
silently replaced. Nothing documents `#n<N>` as reserved.

**Confirmed empirically** — the serialized request, captured from the real
DynamoDB resource before it left the machine:

```python
table.update_item(
    Key={'id': 'x'},
    UpdateExpression='SET #n0 = :v0',
    ExpressionAttributeNames={'#n0': 'colour'},
    ExpressionAttributeValues={':v0': 'red'},
    ConditionExpression=Attr('locked').eq(False),
)
# sent: ExpressionAttributeNames  {'#n0': 'locked'}
#       ExpressionAttributeValues {':v0': {'BOOL': False}}
```

The caller asked to set `colour = 'red'`; the request sets `locked = false`.
Reads corrupt the same way: a `query` whose `FilterExpression='#n0 > :v0'`
binds `score`/`10` is sent filtering `id > 'x'`. The caller's own dicts are
untouched — `copy_dynamodb_params` (`transform.py:35-36`) deep-copies first —
so nothing in the caller's process shows the substitution.

Dropping the `ConditionExpression` keeps the caller's binding, and a
caller placeholder outside the `#n<N>` namespace merges correctly: the defect
is the namespace collision, not the merge.

| Artefact | Path |
|---|---|
| Witness | `harness/dynamodb_placeholder_merge.py` — Phase 1 FAILED, counterexample binds `#n0` on both sides |
| Positive control | `harness/dynamodb_placeholder_merge_fixed.py` — skip-on-collision allocation, both phases SUCCESSFUL |
| Reproducer | `reproducer/finding_f_dynamodb_placeholder_collision.py` — five cases incl. the fix applied to the real objects |
| Issue draft | `bug-reports/finding-f-dynamodb-placeholder-collision.md` — **unfiled** |

Fix shape: reserve the keys already in `params` before the builder runs and
skip a taken suffix, so the condition gets `#n1`/`:v1` and the caller keeps
`#n0`/`:v0`. Demonstrated end to end against the real objects in step 5 of the
reproducer. Rejecting the collision with `ValueError` is the alternative, but
it turns working-if-lucky code into a hard failure; skipping does not. No
buggy control is needed — upstream *is* the buggy variant.

### Row 2 — `disable_threading_if_append_mode` mutates the caller's config

`s3/inject.py:777-795` sets `config.use_threads = False` (`:789`) on the
`TransferConfig` **the caller owns**. A `TransferConfig` is routinely built
once and reused; a single download into an append-mode file object therefore
disables threading for every subsequent transfer sharing that config, with the
warning emitted only on the first. Invariant: a transfer call does not mutate
its `Config` argument. Fix shape: copy before mutating.

### Row 3 — `TransferConfig` alias coherence

`s3/transfer.py:365-370`, `:374-380`, `:382-391`. `__setattr__` propagates
alias → canonical (`max_concurrency` → `max_request_concurrency`,
`max_io_queue` → `max_io_queue_size`) but not the reverse, so assigning the
canonical name leaves the alias stale, and `__getattribute__`'s
`UNSET_DEFAULT` substitution applies to both names independently. Invariant:
the two names of a pair are observationally equal after any assignment to
either. Buggy control: drop the `ALIAS` branch in `__setattr__`.

### Row 4 — `BatchWriter` buffer accounting

`dynamodb/table.py:141-158`. Across a `_flush`, `len(items_to_send) +
len(new_buffer) == len(old_buffer) + len(unprocessed)`, no request is dropped,
and every unprocessed item is retried. This is the row that makes Tier 2
Finding D precise: the same model, with `flush_amount >= 1` assumed, should
verify — which is what shows the non-termination is caused by the missing
guard and not by the loop shape.

---

## Tier 5 — Proofs of absence: DynamoDB expression construction

The boto3 analogue of chromium's review/approval security invariants. Expected
outcome is **SUCCESSFUL** — no bug. The value is the proof, and the buggy
controls are what make it non-vacuous.

| # | Target | Source | Property | Buggy control |
|---|---|---|---|---|
| 1 | Placeholder uniqueness | `conditions.py:422-437`, `:439-461` | Within one `build_expression`, every emitted `#n{k}` and `:v{k}` is distinct: the counters are strictly increasing and incremented exactly once per emission | Drop `self._name_count += 1` at `:432` — two attributes collapse onto `#n0`, so the query silently filters on the wrong attribute |
| 2 | Expression injection safety | `conditions.py:359-420` | Every leaf of the condition tree is replaced by a placeholder before reaching `format` at `:381`: no caller-controlled attribute name or value text is concatenated into the expression string | Route `AttributeBase` values to the literal branch instead of `_build_name_placeholder` at `:412` |
| 3 | Grouped-value identity (`In`) | `conditions.py:444-454` | `len(placeholder_list) == len(value)` and all entries distinct, so `Attr('x').is_in([...])` cannot drop or duplicate a member | Reuse the placeholder for the first element |
| 4 | Key-condition type discipline | `conditions.py:405-411` | With `is_key_condition=True`, every `AttributeBase` leaf is a `Key`; an `Attr` raises `DynamoDBNeedsKeyConditionError` before any placeholder is emitted | Drop the `isinstance(value, Key)` test — an `Attr` reaches `KeyConditionExpression` |
| 5 | Serializer dispatch totality | `types.py:118-155`, `:162-174` | `_get_dynamodb_type` is total over the supported domain and the branches are disjoint — in particular `_is_boolean` **must** precede `_is_number`, since `isinstance(True, int)` is True | Swap the two branches at `:124-128`: `True` serializes as `{'N': '1'}`, so a boolean round-trips as the number 1 |
| 6 | Number round-trip | `types.py:213-217`, `:288-289` | `deserialize(serialize(x)) == x` for every `x` representable in `DYNAMODB_CONTEXT` (38 digits, Emin −128, Emax 126) | Widen `prec` on one side only — the round-trip breaks at the precision boundary |

Row 5 is the sharpest of these: the ordering dependency at `types.py:124-128`
is load-bearing, undocumented, and one refactor away from silently converting
every boolean in a DynamoDB item into the number 1.

---

## Recommended sequence

Ordered by (severity × confidence) ÷ modelling cost, not by tier number.

1. ~~**Tier 4 row 1** — placeholder collision.~~ **Done** — Finding F,
   confirmed on both a write (`update_item`) and a read (`query`). The
   skip-on-collision fix is demonstrated against the real objects in the
   reproducer.
2. ~~**Tier 3 row 1** — `create_tags` `KeyError` after success.~~ **Done** —
   Finding G. One reachability question left open (does EC2 accept a tag with
   no `Value`), stated in the row.
3. **Tier 2 rows C, D** — `preferred_transfer_client`, `BatchWriter`
   non-termination. Both are self-contained; row D exercises the
   unwinding-assertion technique (constraint C5) that later rows reuse.
4. **Tier 5 rows 1, 2, 5** — the proof-of-absence core. Do these before the
   remaining live-bug hunt: they are the deliverable that does not depend on
   finding anything, and row 5's mutation is the most instructive control in
   the plan.
5. **Tier 1 rows 2, 3** — positive controls for Tier 2. Cheap, and they
   strengthen the Tier-2 issue text by showing where boto3 validates correctly.
6. **Tier 2 row B, Tier 3 rows 2, 6** — the remaining plausible live bugs,
   each gated on the reachability question stated in its row.
7. **Tier 3 rows 4, 5 and Tier 4 rows 2–4** — latent preconditions and state
   invariants. Lower expected yield of *live* findings; high value as
   documented invariants.
8. **Tier 3 rows 3, 7 and Tier 5 rows 3, 4, 6** — completeness.

File Findings A, F and G once their issue drafts have been approved; do not
batch them behind the rest of the plan. They are independent — separate issues,
different subsystems.

---

## Cross-cutting workstreams

- **ESBMC-Python frontend issues.** Constraint C1 is filed-worthy today. Keep
  reducing every frontend divergence to a four-case minimal reproducer under
  `reproducer/` before filing, as with the list-parameter case; the prior PoCs
  had three of four such reports fixed upstream, and each fix removed a
  workaround from the harnesses.
- **Reachability is a separate question from the counterexample.** An ESBMC
  witness says the modelled loop can violate its contract; it does not say a
  real caller can get there. Finding A is the cautionary case: the witness and
  the first reproducer both used a client that ignored `PaginationConfig`, and
  on a *paginatable* collection botocore truncates on `MaxItems` before boto3's
  loops run, so `.limit(0)` is masked. The finding survives — 31 bundled
  collections are not paginatable, and negative limits get through on the
  paginatable ones too — but the scope in the first draft was wrong. Check every
  finding against a real service resource with a captured request before
  believing the blast radius, and pin the answer in a test
  (`tests/test_finding_a_reachability.py`).

- **Reproducer discipline.** Each candidate finding gets a script under
  `reproducer/` that drives the real boto3 class, prints the wrong behaviour
  *and* the fixed behaviour side by side, and asserts the difference so it
  fails loudly if upstream changes. Once confirmed it also gets a pytest module
  under `tests/`, parameterised by ESBMC's counterexample rather than by
  hand-chosen values. `finding_a_collection_limit_nonpositive.py`
  is the template.
- **Version drift.** boto3 releases roughly daily. When a reproducer must run
  against a different installed release than the pin, compare the ASTs of the
  functions under test and record the result in the script — not the release
  numbers, which say nothing about the code that matters.
- **Retrospective.** Start `RETROSPECTIVE.md` once the first ESBMC-attributable
  incident lands (an over-specified stub, a vacuous pass, a wrong
  classification). The vLLM and AWS-Neuron retrospectives were most useful for
  the incidents, not the successes.

---

## Out of scope

- **botocore and s3transfer internals.** Tier 2 Finding B stops at boto3's
  boundary: the claim is that boto3 admits the value, not that the dependency
  mishandles it. A defect proven to live in botocore belongs in a separate
  effort against that repository.
- **`boto3/docs/**`.** Documentation generation. No user-facing behaviour, no
  arithmetic, ~1,900 lines that would inflate the target count without
  informing anything.
- **Network, credentials, and session resolution.** `session.py`,
  `crt.py`'s client construction, and the credential chain are I/O and
  configuration-file behaviour — not properties ESBMC-Python can express, and
  better served by the existing functional tests.
- **Thread-safety.** boto3 resources are documented as not thread-safe.
  Concurrent misuse of a shared `ConditionExpressionBuilder` or
  `TransferConfig` is a known limitation, not a finding. (Tier 4 row 2 is in
  scope because it corrupts a shared config from a *single* thread.)
- **Resource-model JSON correctness.** The `.json` resource definitions ship in
  botocore. Tier 3 rows 4 and 5 verify boto3's *handling* of them, and state
  the reachability caveat rather than asserting a live bug.

---

## End-state estimate

| Tier | Target pairs | Expected verdict mix |
|---|---|---|
| 1 | 5 | 5 SUCCESSFUL + 4 buggy FAILED |
| 2 | 5 | 5 FAILED witnesses + 5 SUCCESSFUL controls |
| 3 | 7 | 5–7 FAILED witnesses + 7 SUCCESSFUL controls |
| 4 | 4 | 2–3 FAILED witnesses + 4 SUCCESSFUL controls |
| 5 | 6 | 6 SUCCESSFUL + 6 buggy FAILED |

≈ **27 rows / 54 entry scripts**, comparable to the chromium-dashboard PoC's
48. Expected runtime for `make verify` at that size: 3–6 minutes, dominated by
the `--unwind`-heavy rows (Tier 2 D, Tier 3 4, Tier 4 4).

Realistic finding yield, stated conservatively: **3 confirmed** (Findings A,
F and G), **1 further likely** (Tier 2 row C), and the remainder splitting
between latent preconditions and proofs of absence. The
prior PoCs' pattern held at roughly one-third of candidates surviving to a
filed issue, and that is the right expectation here.

---

## Upstream filing policy

No issue, PR, or comment is opened against `boto/boto3` or `esbmc/esbmc`
without explicit per-item approval. Drafts live under `bug-reports/` marked
**unfiled** until then. A draft is only ready when it has: an ESBMC witness, a
paired positive control, a standalone reproducer against the real code, a
concrete fix shape, and a stated reachability argument.

---

## Provenance

- **ESBMC**: https://github.com/esbmc/esbmc — version 8.4.0, default Bitwuzla
  solver.
- **boto3**: https://github.com/boto/boto3 — pinned at commit `1b554d2`
  (version 1.43.75, 2026-08-20).
- **Reproducers** run against the installed boto3 (1.34.46 here) with an AST
  comparison of the functions under test against the pin recorded in each
  script.
