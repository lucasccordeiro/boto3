#!/usr/bin/env python3
"""
Empirical reproducer — candidate Finding A
  boto3/resources/collection.py:76-87  ResourceCollection.__iter__
  boto3/resources/collection.py:168-184 ResourceCollection.pages
  boto3/resources/collection.py:231-247 ResourceCollection.limit

  `.limit(count)` stores `count` verbatim with no positivity check.
  Both iteration loops count an item *after* consuming it and only then
  compare against the limit, so a non-positive limit yields exactly one
  item -- and issues a real service request -- where the documented
  contract is "Return no more than this many items".

Source (verbatim, boto/boto3 @ 1b554d2):

  # collection.py:76-87  ResourceCollection.__iter__
  limit = self._params.get('limit', None)
  count = 0
  for page in self.pages():
      for item in page:
          yield item                                  # <- item already emitted
          count += 1
          if limit is not None and count >= limit:
              return

  # collection.py:168-184  ResourceCollection.pages
  count = 0
  for page in pages:
      page_items = []
      for item in self._handler(self._parent, params, page):
          page_items.append(item)                     # <- item already kept
          count += 1
          if limit is not None and count >= limit:
              break
      yield page_items
      if limit is not None and count >= limit:
          break

  # collection.py:231-247  ResourceCollection.limit
  def limit(self, count):
      # docstring, verbatim:
      #     :param count: Return no more than this many items
      return self._clone(limit=count)                 # <- no validation

This script drives the REAL ResourceCollection class with a fake client
and handler, so the loops under test are boto3's own, not a re-typing of
them.

Dependencies: boto3 (any version whose ResourceCollection.__iter__ /
.pages bodies match the pin -- see the version note at the bottom).
"""

import boto3
from boto3.resources.collection import ResourceCollection


# ── Minimal doubles: only the attributes ResourceCollection reads ────


class _Meta:
    service_name = 'fake'

    def __init__(self, client):
        self.client = client


class _Client:
    """Stands in for a paginating botocore client."""

    def __init__(self, pages):
        self._pages = pages
        self.calls = 0
        self.pagination_config = None

    def can_paginate(self, operation_name):
        return True

    def get_paginator(self, operation_name):
        return self

    def paginate(self, **kwargs):
        self.calls += 1
        self.pagination_config = kwargs.get('PaginationConfig')
        return list(self._pages)


class _Parent:
    def __init__(self, client):
        self.meta = _Meta(client)


class _Request:
    operation = 'ListThings'
    params = []


class _Resource:
    type = 'Thing'


class _Model:
    request = _Request()
    resource = _Resource()


def _handler(parent, params, page):
    """Stands in for ResourceHandler: turns a raw page into resources."""
    return list(page['Items'])


SERVICE_PAGES = [{'Items': ['a', 'b', 'c']}, {'Items': ['d', 'e']}]


def collect(limit):
    client = _Client(SERVICE_PAGES)
    collection = ResourceCollection(_Model(), _Parent(client), _handler)
    if limit is None:
        return list(collection.all()), client
    return list(collection.limit(limit)), client


# ── Step 1: positive limits behave as documented ─────────────────────

print(f'boto3 {boto3.__version__}')
print()
print('Positive limits (correct):')
for n in (1, 2, 3, 5, 99):
    got, fake = collect(n)
    assert len(got) <= n, f'limit={n} returned {len(got)} items'
    print(f'  .limit({n:>2}) -> {got!r}')

# ── Step 2: non-positive limits return one item anyway ───────────────

print()
print('Non-positive limits (the defect):')
violations = []
for n in (0, -1, -5):
    got, fake = collect(n)
    print(
        f'  .limit({n:>2}) -> {got!r}   '
        f'service requests issued: {fake.calls}, '
        f'PaginationConfig={fake.pagination_config!r}'
    )
    if len(got) > max(n, 0):
        violations.append((n, got))

assert violations, 'expected .limit(<=0) to over-deliver'
for n, got in violations:
    assert len(got) == 1, f'limit={n}: expected 1 item, got {len(got)}'
print()
print(
    f'  -> {len(violations)} non-positive limits each returned 1 item '
    f'instead of 0, after a real service request.'
)

# ── Step 3: contrast — what the fix would produce ────────────────────


def collect_fixed(limit):
    """The proposed fix: test the limit before consuming an item."""
    out = []
    count = 0
    for page in SERVICE_PAGES:
        for item in page['Items']:
            if limit is not None and count >= limit:
                return out
            out.append(item)
            count += 1
    return out


print()
print('With the guard hoisted ahead of the item (proposed fix):')
for n in (0, -1, -5, 1, 3):
    got = collect_fixed(n)
    assert len(got) <= max(n, 0)
    print(f'  .limit({n:>2}) -> {got!r}')

print()
print('Reproduced: .limit(0) and .limit(-n) deliver one resource each.')

# Version note: this was run against the installed boto3 above. The
# bodies of ResourceCollection.__iter__, .pages, .limit and .page_size
# are AST-identical between that release and the pin (1b554d2,
# boto3 1.43.75), so the observation carries to the pinned source:
#
#   python3 - <<'EOF'
#   import ast
#   def body(path, cls, name):
#       tree = ast.parse(open(path).read())
#       for n in ast.walk(tree):
#           if isinstance(n, ast.ClassDef) and n.name == cls:
#               for f in n.body:
#                   if isinstance(f, ast.FunctionDef) and f.name == name:
#                       b = f.body
#                       if isinstance(b[0].value, ast.Constant): b = b[1:]
#                       return ast.dump(ast.Module(body=b, type_ignores=[]))
#   EOF
