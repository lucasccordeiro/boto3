# SPDX-License-Identifier: Apache-2.0
# Target: live-bug witness for ResourceCollection.limit(n), n <= 0.
# Candidate Finding A (see ROADMAP.md Tier 2).
#
# Trace against pinned boto/boto3 @ 1b554d2:
#
#   1. `ResourceCollection.limit(count)` (collection.py:231-247) stores
#      the value verbatim: `return self._clone(limit=count)`. There is
#      no positivity check anywhere on the path -- not in `limit()`,
#      not in `_clone`, not in `__init__`.
#
#   2. `pages()` (collection.py:168-184) counts an item and only then
#      compares:
#          for item in self._handler(...):
#              page_items.append(item)      # :172  item already kept
#              count += 1                   # :176
#              if limit is not None and count >= limit:
#                  break                    # :178
#      With limit <= 0 the guard fires on the first item, but that item
#      is already in `page_items`, and the page is yielded at :180.
#
#   3. `__iter__` (collection.py:76-87) repeats the same shape:
#          for item in page:
#              yield item                   # :81  item already emitted
#              count += 1                   # :85
#              if limit is not None and count >= limit:
#                  return                   # :87
#      So the one item that survived step 2 is handed to the caller.
#
# Net effect: `.limit(0)` -- and `.limit(-n)` for any n -- performs a
# real service request and yields exactly ONE resource, where the
# documented contract is "Return no more than this many items"
# (collection.py:244). The off-by-one is invisible for every positive
# limit, which is why it survives the test suite.
#
# Harness shape: inline both loops verbatim over a symbolic limit and
# a symbolic page length. The item payload is irrelevant -- neither
# loop inspects it -- so a page is modelled by its length. The
# Phase-1 assertion is the documented contract `n <= limit`; ESBMC's
# counterexample is any limit <= 0, at which the observed count is 1.
#
# Both loops are inlined in a single function to keep list aliasing
# out of the model; the block comments mark which upstream lines each
# block reproduces.
#
# Phase 1 expected: FAILED (the counterexample IS the bug report).
# Phase 2: skipped.

from stubs import nondet_int, __ESBMC_assume

NPAGES = 2


def collection_iter_count(page_len: int, limit: int) -> int:
    """Items delivered to the caller of `for x in coll.limit(limit)`.

    Models the limit-is-set path only (`limit is not None`), which is
    the only path `.limit()` can produce.
    """
    # --- ResourceCollection.pages(), collection.py:168-184 ----------
    # Records len(page_items) per yielded page in `page_sizes`.
    page_sizes = [0, 0]
    pages_yielded = 0
    count = 0
    p = 0
    while p < NPAGES:
        page_items = 0
        i = 0
        while i < page_len:
            page_items = page_items + 1     # :172 page_items.append(item)
            count = count + 1               # :176 count += 1
            if count >= limit:              # :177
                break                       # :178
            i = i + 1
        page_sizes[pages_yielded] = page_items
        pages_yielded = pages_yielded + 1   # :180 yield page_items
        if count >= limit:                  # :183
            break                           # :184
        p = p + 1

    # --- ResourceCollection.__iter__, collection.py:76-87 -----------
    delivered = 0
    q = 0
    while q < pages_yielded:
        k = 0
        while k < page_sizes[q]:
            delivered = delivered + 1       # :81 yield item / :85 count += 1
            if delivered >= limit:          # :86
                return delivered            # :87
            k = k + 1
        q = q + 1
    return delivered


def main() -> None:
    limit = nondet_int()
    __ESBMC_assume(-3 <= limit)
    __ESBMC_assume(limit <= 3)

    # A non-empty first page: the service returned at least one
    # resource. This is the ordinary case, not a corner case.
    page_len = nondet_int()
    __ESBMC_assume(1 <= page_len)
    __ESBMC_assume(page_len <= 3)

    delivered = collection_iter_count(page_len, limit)

    # Documented contract, collection.py:244:
    #   ":param count: Return no more than this many items"
    # Violated at every limit <= 0, where delivered == 1.
    assert delivered <= limit


main()
