# Finding A — upstream issue report (DRAFT, not filed)

**Status:** drafted, **unfiled**. Filing requires explicit approval — see
[`ROADMAP.md`](../ROADMAP.md) "Upstream filing policy".

GitHub issue text for `boto/boto3`, formatted to the repository's
`bug-report.yml` template. Verified against upstream at commit `1b554d2`
(boto3 1.43.75); empirically reproduced with the script at
[`../reproducer/finding_a_collection_limit_nonpositive.py`](../reproducer/finding_a_collection_limit_nonpositive.py).

- **Title:** `ResourceCollection.limit(0)` returns one resource instead of none
- **Labels:** `bug`, `needs-triage`

---

**Describe the bug**

`ResourceCollection.limit(count)` documents `count` as "Return no more than
this many items" (`boto3/resources/collection.py:244`), but for `count == 0`
— and for any negative `count` — the collection issues a real service request
and yields **exactly one resource**.

The value is stored without validation:

```python
# boto3/resources/collection.py:231-247
def limit(self, count):
    # :param count: Return no more than this many items
    return self._clone(limit=count)
```

and both iteration loops consume an item *before* testing the limit:

```python
# boto3/resources/collection.py:168-184  ResourceCollection.pages
for item in self._handler(self._parent, params, page):
    page_items.append(item)                     # item already kept
    count += 1
    if limit is not None and count >= limit:    # 1 >= 0 -> fires, too late
        break

# boto3/resources/collection.py:76-87  ResourceCollection.__iter__
for item in page:
    yield item                                  # item already emitted
    count += 1
    if limit is not None and count >= limit:
        return
```

With `limit = 0` the guard fires on the first item in both loops, but that
item has already been appended and yielded. The off-by-one is invisible for
every positive limit, which is why it is not caught by the existing tests.

**Regression Issue**

No — the loop shape dates back to the original collections implementation.

**Expected Behavior**

`collection.limit(0)` yields no resources. A negative `count` either yields no
resources or raises `ValueError` at the `limit()` call — rejecting it at
`limit()` would also stop a negative value reaching botocore's `MaxItems`,
where it is used as a slice bound.

**Current Behavior**

`s3.buckets.limit(0)` yields one bucket, after issuing a real `ListBuckets`
request. `limit(-1)` and `limit(-5)` behave identically.

Which collections are affected depends on the branch taken at
`collection.py:147-164`:

| Collection | `.limit(0)` | `.limit(-n)` |
|---|---|---|
| Not paginatable — `s3.buckets`, `ec2.key_pairs`, `ec2.classic_addresses`, `ec2.vpc_addresses`, `iam.saml_providers`, `opsworks.stacks`, and 25 others | **1 resource** | **1 resource** |
| Paginatable — `bucket.objects`, and most others | 0 (see below) | **1 resource** while `n < page_len` |

For paginatable collections the limit is *also* passed to botocore as
`PaginationConfig={'MaxItems': limit}`, and `PageIterator._truncate_response`
keeps `original[:max_items]` (`botocore/paginate.py:291-296`, `:421-448`). At
`limit == 0` that slice is empty, so the loops below never see an item and the
defect is hidden. At a small negative limit the slice is *negative* — it drops
the last `n` items instead of truncating to zero — so a page still arrives and
the loops yield one resource from it.

**Reproduction Steps**

```python
import boto3
from boto3.resources.collection import ResourceCollection


class _Meta:
    service_name = 'fake'
    def __init__(self, client): self.client = client

class _Client:
    def can_paginate(self, name): return True
    def get_paginator(self, name): return self
    def paginate(self, **kwargs):
        return [{'Items': ['a', 'b', 'c']}, {'Items': ['d', 'e']}]

class _Parent:
    def __init__(self, client): self.meta = _Meta(client)

class _Request:
    operation = 'ListThings'
    params = []

class _Model:
    request = _Request()
    resource = type('R', (), {'type': 'Thing'})()


def handler(parent, params, page):
    return list(page['Items'])


coll = ResourceCollection(_Model(), _Parent(_Client()), handler)
print(list(coll.limit(3)))   # ['a', 'b', 'c']   correct
print(list(coll.limit(0)))   # ['a']             expected []
print(list(coll.limit(-5)))  # ['a']             expected [] (or ValueError)
```

Against a real service resource, using the non-paginatable `s3.buckets`:

```python
import boto3

s3 = boto3.resource('s3')
print(len(list(s3.buckets.limit(0))))   # 1, expected 0
print(len(list(s3.buckets.limit(-5))))  # 1, expected 0
print(len(list(s3.buckets.limit(2))))   # 2, correct
```

and on a paginatable one, where only negative limits get through:

```python
objects = s3.Bucket('some-bucket').objects   # assume >2 objects
print(len(list(objects.limit(0))))    # 0, masked by botocore
print(len(list(objects.limit(-1))))   # 1, expected 0
```

**Possible Solution**

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

This also fixes the negative case. If rejecting negatives outright is
preferred, add a check in `limit()`:

```python
def limit(self, count):
    if count is not None and count < 0:
        raise ValueError('count must be non-negative')
    return self._clone(limit=count)
```

**Additional Information/Context**

- Affected code (commit `1b554d2`):
  - `boto3/resources/collection.py:76-87` — `ResourceCollection.__iter__`
  - `boto3/resources/collection.py:168-184` — `ResourceCollection.pages`
  - `boto3/resources/collection.py:231-247` — `ResourceCollection.limit`
- Reachability was checked against the bundled resource models: 31 of the
  278 collections defined in `boto3/data/**/resources-1.json` use a
  non-paginatable operation and therefore take the `else` branch at
  `collection.py:164`, where these loops are the only thing enforcing the
  limit.
- `page_size(count)` (`collection.py:249-260`) is unvalidated on the same
  path; `PageSize: 0` / a negative page size is forwarded verbatim to
  botocore's `PaginationConfig` at `collection.py:154`.
- Found with [ESBMC](https://github.com/esbmc/esbmc) bounded model checking
  over both loops with a symbolic limit; the counterexample is `limit = 0`,
  `delivered = 1`. Confirmed with a standalone reproducer driving the real
  `ResourceCollection` class.

**SDK version used**

1.43.75 (commit `1b554d2`); also reproduced on 1.34.46 — the bodies of
`__iter__`, `pages`, `limit` and `page_size` are AST-identical across the two.

**Environment details (OS name and version, etc.)**

Linux x86_64, CPython 3.12.3.
