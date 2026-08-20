# SPDX-License-Identifier: Apache-2.0
"""Which real collections actually reach Finding A.

`ResourceCollection.pages()` has two branches (collection.py:145-164):

  * `client.can_paginate(op)` is True -- the limit is also handed to
    botocore as `PaginationConfig={'MaxItems': limit}`, and botocore
    truncates to zero items before boto3's loops run. The defect is
    **masked**.
  * otherwise -- a single un-truncated call at :164, and the loops at
    :168-184 / :76-87 are the only thing enforcing the limit. The
    defect is **live**. On boto3 1.43.76 seven of the 88 bundled
    collections take this branch: `ec2.key_pairs`,
    `ec2.placement_groups`, `ec2.classic_addresses`,
    `ec2.vpc_addresses`, `Instance.vpc_addresses`,
    `cloudwatch Metric.alarms` and `iam.saml_providers`.

Both are pinned here against real service resources, because the
distinction is the whole reachability argument and a synthetic client
that ignores `PaginationConfig` would show the bug everywhere.
"""

import boto3
import pytest

from aws_capture import capturing_resource

DESCRIBE_KEY_PAIRS = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<DescribeKeyPairsResponse xmlns="http://ec2.amazonaws.com/doc/2016-11-15/">'
    b"<requestId>r-1</requestId><keySet>"
    b"<item><keyName>alpha</keyName><keyFingerprint>aa:aa</keyFingerprint></item>"
    b"<item><keyName>beta</keyName><keyFingerprint>bb:bb</keyFingerprint></item>"
    b"<item><keyName>gamma</keyName><keyFingerprint>cc:cc</keyFingerprint></item>"
    b"</keySet></DescribeKeyPairsResponse>"
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


def test_describe_key_pairs_is_not_paginatable():
    """The precondition that puts ec2.key_pairs on the defective branch.

    Asserted rather than assumed, because it drifts: `s3.buckets` was on
    this branch until botocore added a `ListBuckets` paginator, which
    silently moved it to the masked one. If AWS adds a `DescribeKeyPairs`
    paginator, this fails instead of the suite quietly proving nothing.
    """
    ec2 = boto3.client(
        "ec2",
        region_name="us-east-1",
        aws_access_key_id="dummy",
        aws_secret_access_key="dummy",
    )
    s3 = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="dummy",
        aws_secret_access_key="dummy",
    )
    assert ec2.can_paginate("describe_key_pairs") is False
    assert s3.can_paginate("list_objects_v2") is True


@pytest.mark.parametrize("limit", [0, -1, -5])
def test_non_paginated_collection_returns_one_resource(limit):
    """Live: ec2.key_pairs.limit(0) hands back a key the caller excluded."""
    sent = []
    ec2 = capturing_resource("ec2", DESCRIBE_KEY_PAIRS, sent)

    names = [kp.name for kp in ec2.key_pairs.limit(limit)]

    assert len(sent) == 1, "a real DescribeKeyPairs request was issued"
    assert names == ["alpha"], (
        f"ec2.key_pairs.limit({limit}) returned {names}; contract allows none"
    )


def test_non_paginated_collection_honours_positive_limits():
    """Control: invisible for every positive limit."""
    sent = []
    ec2 = capturing_resource("ec2", DESCRIBE_KEY_PAIRS, sent)
    assert [kp.name for kp in ec2.key_pairs.limit(2)] == ["alpha", "beta"]


PAGE_LEN = 3  # the canned ListObjectsV2 response above


@pytest.mark.parametrize("limit", [0, -PAGE_LEN, -PAGE_LEN - 2])
def test_paginated_collection_masked_when_botocore_empties_the_page(limit):
    """Masked: botocore keeps `page[:limit]`, which is empty here.

    botocore's `PageIterator.__iter__` computes
    `truncate_amount = total_items + num_current_response - max_items`
    (paginate.py:295-297 on develop), so `_truncate_response` (:429-459)
    keeps `original[:max_items]`. At
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
