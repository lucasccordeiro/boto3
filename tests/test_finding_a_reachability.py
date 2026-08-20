# SPDX-License-Identifier: Apache-2.0
"""Which real collections actually reach Finding A.

`ResourceCollection.pages()` has two branches (collection.py:147-164):

  * `client.can_paginate(op)` is True -- the limit is also handed to
    botocore as `PaginationConfig={'MaxItems': limit}`, and botocore
    truncates to zero items before boto3's loops run. The defect is
    **masked**.
  * otherwise -- a single un-truncated call at :164, and the loops at
    :168-184 / :76-87 are the only thing enforcing the limit. The
    defect is **live**.

Both are pinned here against real service resources, because the
distinction is the whole reachability argument and a synthetic client
that ignores `PaginationConfig` would show the bug everywhere.
"""

import boto3
import pytest

from aws_capture import capturing_resource

LIST_BUCKETS = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<ListAllMyBucketsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    b"<Owner><ID>oid</ID><DisplayName>me</DisplayName></Owner><Buckets>"
    b"<Bucket><Name>alpha</Name><CreationDate>2020-01-01T00:00:00.000Z</CreationDate></Bucket>"
    b"<Bucket><Name>beta</Name><CreationDate>2020-01-01T00:00:00.000Z</CreationDate></Bucket>"
    b"<Bucket><Name>gamma</Name><CreationDate>2020-01-01T00:00:00.000Z</CreationDate></Bucket>"
    b"</Buckets></ListAllMyBucketsResult>"
)

LIST_OBJECTS = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    b"<Name>b</Name><Prefix></Prefix><KeyCount>3</KeyCount><MaxKeys>1000</MaxKeys>"
    b"<IsTruncated>false</IsTruncated>"
    b"<Contents><Key>a.txt</Key><LastModified>2020-01-01T00:00:00.000Z</LastModified>"
    b'<ETag>"1"</ETag><Size>1</Size><StorageClass>STANDARD</StorageClass></Contents>'
    b"<Contents><Key>b.txt</Key><LastModified>2020-01-01T00:00:00.000Z</LastModified>"
    b'<ETag>"2"</ETag><Size>2</Size><StorageClass>STANDARD</StorageClass></Contents>'
    b"<Contents><Key>c.txt</Key><LastModified>2020-01-01T00:00:00.000Z</LastModified>"
    b'<ETag>"3"</ETag><Size>3</Size><StorageClass>STANDARD</StorageClass></Contents>'
    b"</ListBucketResult>"
)


def test_list_buckets_is_not_paginatable():
    """The precondition that puts s3.buckets on the defective branch."""
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="dummy",
        aws_secret_access_key="dummy",
    )
    assert client.can_paginate("list_buckets") is False
    assert client.can_paginate("list_objects_v2") is True


@pytest.mark.parametrize("limit", [0, -1, -5])
def test_non_paginated_collection_returns_one_resource(limit):
    """Live: s3.buckets.limit(0) hands back a bucket the caller excluded."""
    sent = []
    s3 = capturing_resource("s3", LIST_BUCKETS, sent)

    names = [bucket.name for bucket in s3.buckets.limit(limit)]

    assert len(sent) == 1, "a real ListBuckets request was issued"
    assert names == ["alpha"], (
        f"s3.buckets.limit({limit}) returned {names}; contract allows none"
    )


def test_non_paginated_collection_honours_positive_limits():
    """Control: invisible for every positive limit."""
    sent = []
    s3 = capturing_resource("s3", LIST_BUCKETS, sent)
    assert [b.name for b in s3.buckets.limit(2)] == ["alpha", "beta"]


PAGE_LEN = 3  # the canned ListObjectsV2 response above


@pytest.mark.parametrize("limit", [0, -PAGE_LEN, -PAGE_LEN - 2])
def test_paginated_collection_masked_when_botocore_empties_the_page(limit):
    """Masked: botocore keeps `page[:limit]`, which is empty here.

    paginate.py:291-296 computes
    `truncate_amount = total_items + num_current_response - max_items`,
    so `_truncate_response` keeps `original[:max_items]`. At
    `limit == 0`, or `-limit >= page_len`, that slice is empty and
    boto3's loops never see an item.
    """
    sent = []
    s3 = capturing_resource("s3", LIST_OBJECTS, sent)

    keys = [obj.key for obj in s3.Bucket("my-bucket").objects.limit(limit)]

    assert keys == [], f"expected an empty page, got {keys}"
    assert len(sent) == 1, "the request is still issued even at limit<=0"


@pytest.mark.parametrize("limit", [-1, -2])
def test_paginated_collection_is_live_for_small_negative_limits(limit):
    """Live on the paginated path too, via botocore's negative slice.

    `original[:max_items]` with a negative `max_items` drops the last
    `|limit|` items rather than truncating to zero, so a page still
    arrives and boto3's loops yield one item from it. The defect is
    therefore not confined to the non-paginated branch: it reaches any
    collection whenever `0 < -limit < page_len`.
    """
    sent = []
    s3 = capturing_resource("s3", LIST_OBJECTS, sent)

    keys = [obj.key for obj in s3.Bucket("my-bucket").objects.limit(limit)]

    assert len(sent) == 1
    assert keys == ["a.txt"], (
        f"objects.limit({limit}) returned {keys}; contract allows none"
    )
