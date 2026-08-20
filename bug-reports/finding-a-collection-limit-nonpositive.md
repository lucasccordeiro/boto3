# Finding A — ready to file at `boto/boto3`

**Status: not filed.** Paste the sections below into the fields of
<https://github.com/boto/boto3/issues/new?template=bug-report.yml>.

Verified against boto3 **1.43.76** / botocore 1.43.76 (current at 2026-08-20)
and against `develop` at commit `ced31bb7`, where `ResourceCollection.__iter__`
still yields the item before checking the limit.

**Prior art — [#4670](https://github.com/boto/boto3/issues/4670), and this
report exists to correct it.** That issue reported exactly this in Dec 2025.
A maintainer asked whether it reproduced "with a real operation"; the reporter
tested, got `[]`, and it was closed as not reproducible. **Both were testing a
paginatable collection**, where botocore truncates on `MaxItems` before boto3's
loops run. On a non-paginatable collection it does reproduce with a real
operation. GitHub's bot on that issue asks for a new issue referencing it, so
that is the route — lead with the real-operation reproduction, since its absence
is the sole reason #4670 was closed.

**Before filing:** replace *Environment details* with your own machine, and
re-run the reproduction — the affected set drifts (see the note in *Additional
Information*).

---

## Title

```
ResourceCollection.limit(0) yields one resource on non-paginatable collections
```

## Describe the bug

`ResourceCollection.limit(count)` documents `count` as "Return no more than this
many items" (`boto3/resources/collection.py:244`), but stores it without
validation, and both iteration loops consume an item *before* testing the limit:

```python
# boto3/resources/collection.py:168-184  pages
for item in self._handler(self._parent, params, page):
    page_items.append(item)                     # item already kept
    count += 1
    if limit is not None and count >= limit:    # 1 >= 0 -> too late
        break

# boto3/resources/collection.py:76-87  __iter__
for item in page:
    yield item                                  # item already emitted
    count += 1
    if limit is not None and count >= limit:
        return
```

`pages()` branches on `client.can_paginate` (`collection.py:145-164`). On the
paginatable branch the limit is *also* sent to botocore as
`PaginationConfig={'MaxItems': limit}`, and truncation there hides the defect —
which is why #4670 was closed. On the non-paginatable branch (`:164`) there is
no truncation, and these loops are the only thing enforcing the limit.

## Regression Issue

No. Present since the collections implementation was introduced; still on
`develop` at `ced31bb7`.

## Expected Behavior

`limit(0)` yields no resources. A negative `count` yields none, or raises
`ValueError` at the `limit()` call — which would also stop a negative value
reaching botocore's `MaxItems`, where it is used as a slice bound.

## Current Behavior

`ec2.key_pairs.limit(0)` yields one key pair, after issuing a real
`DescribeKeyPairs` request. `limit(-1)` and `limit(-5)` behave identically:

```
ec2.key_pairs.limit(None) -> ['alpha', 'beta', 'gamma']
ec2.key_pairs.limit(   2) -> ['alpha', 'beta']
ec2.key_pairs.limit(   0) -> ['alpha']            expected []
ec2.key_pairs.limit(  -5) -> ['alpha']            expected []
```

Paginatable collections are affected too, for small negative limits.
`_truncate_response` keeps `original[:max_items]`
(botocore `PageIterator.__iter__` at `paginate.py:295-297`, `_truncate_response` at `:429-459`, on botocore `develop`); a negative `max_items` makes that
a negative slice, dropping the last `n` items instead of truncating to zero, so
a page still arrives and the loops yield one resource from it:

```
bucket.objects.limit( 0) -> []                    masked
bucket.objects.limit(-1) -> ['a.txt']             expected []
```

## Reproduction Steps

Against a real account, no stubbing:

```python
import boto3

ec2 = boto3.resource('ec2')            # needs >=2 key pairs in the region
print([k.name for k in ec2.key_pairs.limit(0)])    # ['<first>'], expected []
print([k.name for k in ec2.key_pairs.limit(-5)])   # ['<first>'], expected []
print([k.name for k in ec2.key_pairs.limit(2)])    # 2 names, correct
```

`ec2.key_pairs` is used because `DescribeKeyPairs` has no paginator, which is
what puts it on the branch at `collection.py:164`. Any of the seven collections
listed below behaves the same. Using a paginatable collection such as
`bucket.objects` is what made #4670 look irreproducible.

## Possible Solution

Test the limit before consuming the item, in both loops:

```python
# collection.py:168-184
for item in self._handler(self._parent, params, page):
    if limit is not None and count >= limit:
        break
    page_items.append(item)
    count += 1

# collection.py:76-87
for item in page:
    if limit is not None and count >= limit:
        return
    yield item
    count += 1
```

This also fixes the negative case. Alternatively, or additionally, reject a
negative `count` in `limit()`.

## Additional Information/Context

- Affected code: `boto3/resources/collection.py:76-87` (`__iter__`),
  `:168-184` (`pages`), `:231-247` (`limit`).
- On boto3 1.43.76, 7 of the 88 collections in `boto3/data/**/resources-1.json`
  use a non-paginatable operation: `ec2.key_pairs`, `ec2.placement_groups`,
  `ec2.classic_addresses`, `ec2.vpc_addresses`, `Instance.vpc_addresses`,
  `cloudwatch Metric.alarms`, `iam.saml_providers`.
- That set drifts with botocore's paginator data. `s3.buckets` was on it until
  botocore added a `ListBuckets` paginator, which silently moved it to the
  masked branch — so a collection that reproduces today may stop, and vice
  versa, without boto3 changing at all.
- `page_size(count)` (`collection.py:249-260`) is unvalidated on the same path.
- Supersedes #4670, closed because it was only reproduced with a synthetic
  `ResourceCollection` subclass.
- Found with [ESBMC](https://github.com/esbmc/esbmc) bounded model checking over
  both loops with a symbolic limit; the counterexample is `limit = 0`,
  `delivered = 1`.

## SDK version used

```
boto3 1.43.76 / botocore 1.43.76; also reproduced on 1.34.46. Code path unchanged on develop at ced31bb7.
```

## Environment details (OS name and version, etc.)

```
Ubuntu 24.04.4 LTS, x86_64, CPython 3.12.3
```
