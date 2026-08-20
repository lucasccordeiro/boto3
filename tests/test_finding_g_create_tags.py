# SPDX-License-Identifier: Apache-2.0
"""Finding G, driven by ESBMC's counterexample.

`harness/create_tags_missing_value.py` fails Phase 1; ESBMC's
counterexample is recorded in `generated/test_create_tags_missing_value.py`
and is applied here to the real EC2 service resource.

  boto3/ec2/createtags.py:27  the CreateTags request
  boto3/ec2/createtags.py:38  tag['Key'] / tag['Value'], unguarded
"""

import botocore.session
import pytest

from aws_capture import capturing_resource
from esbmc_witness import witness

CREATE_TAGS_OK = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<CreateTagsResponse xmlns="http://ec2.amazonaws.com/doc/2016-11-15/">'
    b"<requestId>req-1</requestId><return>true</return>"
    b"</CreateTagsResponse>"
)

WITNESS = witness("create_tags_missing_value")
N_TAGS = 3

# ESBMC numbers the per-iteration nondets in source order: the tag at
# index i has its Key flag in `k`/`k1`/`k2` and its Value flag in
# `v`/`v1`/`v2`.
TAG_FLAGS = [
    (WITNESS[f"k{i or ''}"], WITNESS[f"v{i or ''}"]) for i in range(N_TAGS)
]
N_RESOURCES = WITNESS["n_resources"]


def _tags():
    tags = []
    for i, (has_key, has_value) in enumerate(TAG_FLAGS):
        tag = {}
        if has_key:
            tag["Key"] = f"key{i}"
        if has_value:
            tag["Value"] = f"value{i}"
        tags.append(tag)
    return tags


def _resources():
    return [f"i-{i:017x}" for i in range(N_RESOURCES)]


def _first_missing_member():
    """The member createtags.py:38 reaches for first and does not find."""
    for has_key, has_value in TAG_FLAGS:
        if not has_key:
            return "Key"
        if not has_value:
            return "Value"
    return None


def test_witness_omits_a_tag_member():
    """Guard: a regenerated counterexample must still omit a member."""
    assert _first_missing_member() is not None, (
        f"ESBMC witness {TAG_FLAGS} has every member present"
    )
    assert N_RESOURCES >= 1, "no resources means the loop body never runs"


def test_partial_tag_passes_botocore_validation():
    """The precondition the finding rests on: nothing is required."""
    shape = botocore.session.get_session().get_service_model("ec2").shape_for("Tag")
    assert shape.required_members == []


def test_create_tags_raises_after_the_request_is_sent():
    """The bug. Fails once upstream reads the tag with `.get`."""
    sent = []
    ec2 = capturing_resource("ec2", CREATE_TAGS_OK, sent)

    with pytest.raises(KeyError) as excinfo:
        ec2.create_tags(Resources=_resources(), Tags=_tags())

    assert excinfo.value.args[0] == _first_missing_member()
    assert len(sent) == 1, "the CreateTags request must already have been issued"
    assert "Action=CreateTags" in sent[0]
    assert "Tag.1.Key=key0" in sent[0]


def test_complete_tags_are_unaffected():
    """Control: the happy path returns Tag resources and raises nothing."""
    sent = []
    ec2 = capturing_resource("ec2", CREATE_TAGS_OK, sent)

    created = ec2.create_tags(
        Resources=_resources()[:1], Tags=[{"Key": "env", "Value": "prod"}]
    )

    assert len(created) == 1
    assert (created[0].key, created[0].value) == ("env", "prod")


def test_fix_reading_the_tag_with_get_returns_resources():
    """The proposed fix, on the same witness inputs."""
    sent = []
    ec2 = capturing_resource("ec2", CREATE_TAGS_OK, sent)

    def create_tags_fixed(self, **kwargs):
        self.meta.client.create_tags(**kwargs)
        return [
            self.Tag(resource, tag.get("Key"), tag.get("Value", ""))
            for resource in kwargs.get("Resources", [])
            for tag in kwargs.get("Tags", [])
        ]

    created = create_tags_fixed(ec2, Resources=_resources(), Tags=_tags())

    assert len(created) == N_RESOURCES * N_TAGS
    assert all(tag.value is not None for tag in created)
