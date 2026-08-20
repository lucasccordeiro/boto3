# SPDX-License-Identifier: Apache-2.0
"""Finding A, driven by ESBMC's counterexample.

`harness/collection_limit_nonpositive.py` fails Phase 1; ESBMC's
counterexample is recorded in `generated/test_collection_limit_nonpositive.py`
and is applied here to the real `ResourceCollection`.

  boto3/resources/collection.py:76-87   __iter__
  boto3/resources/collection.py:168-184 pages
  boto3/resources/collection.py:231-247 limit
"""

from boto3.resources.collection import ResourceCollection

from esbmc_witness import witness_values

LIMIT, PAGE_LEN = witness_values(
    "collection_limit_nonpositive", "limit", "page_len"
)


class _Client:
    def __init__(self, pages):
        self._pages = pages
        self.calls = 0

    def can_paginate(self, operation_name):
        return True

    def get_paginator(self, operation_name):
        return self

    def paginate(self, **kwargs):
        self.calls += 1
        return list(self._pages)


class _Parent:
    def __init__(self, client):
        self.meta = type("Meta", (), {"service_name": "fake", "client": client})()


class _Model:
    request = type("Request", (), {"operation": "ListThings", "params": []})()
    resource = type("Resource", (), {"type": "Thing"})()


def _collect(limit, page_len):
    pages = [{"Items": [f"item{i}" for i in range(page_len)]} for _ in range(2)]
    client = _Client(pages)
    collection = ResourceCollection(
        _Model(), _Parent(client), lambda parent, params, page: list(page["Items"])
    )
    return list(collection.limit(limit)), client


def test_witness_is_the_nonpositive_limit_case():
    """Guard: a regenerated counterexample must still exercise the bug."""
    assert LIMIT <= 0, f"ESBMC witness limit={LIMIT} is not the defect case"
    assert PAGE_LEN >= 1, f"ESBMC witness page_len={PAGE_LEN} yields no items"


def test_nonpositive_limit_issues_a_request_and_returns_one_resource():
    """The bug. Fails once upstream tests the limit before the item."""
    delivered, client = _collect(LIMIT, PAGE_LEN)

    assert client.calls == 1, "expected a real service request to be issued"
    assert len(delivered) == 1, (
        f"limit={LIMIT} delivered {len(delivered)} resources; "
        f"the documented contract allows {max(LIMIT, 0)}"
    )


def test_positive_limits_are_honoured():
    """Control: the off-by-one is invisible for every positive limit."""
    for n in (1, 2, 3, 5, 99):
        delivered, _ = _collect(n, PAGE_LEN)
        assert len(delivered) <= n


def test_fix_hoisting_the_guard_delivers_nothing():
    """The proposed fix, on the same witness inputs."""

    def collect_fixed(limit, page_len):
        out = []
        count = 0
        for _ in range(2):
            for item in [f"item{i}" for i in range(page_len)]:
                if limit is not None and count >= limit:
                    return out
                out.append(item)
                count += 1
        return out

    assert len(collect_fixed(LIMIT, PAGE_LEN)) == 0
    assert len(collect_fixed(2, PAGE_LEN)) == 2
