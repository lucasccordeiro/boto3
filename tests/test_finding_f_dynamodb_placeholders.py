# SPDX-License-Identifier: Apache-2.0
"""Finding F, driven by ESBMC's counterexample.

`harness/dynamodb_placeholder_merge.py` fails Phase 1; ESBMC's
counterexample is recorded in `generated/test_dynamodb_placeholder_merge.py`
and is applied here to the real DynamoDB resource.

  boto3/dynamodb/transform.py:172     the unconditional reset() to #n0/:v0
  boto3/dynamodb/transform.py:203-213 the two dict.update merges
  boto3/dynamodb/conditions.py:313-322 placeholder generation
"""

import json

from boto3.dynamodb.conditions import Attr

from aws_capture import capturing_resource
from esbmc_witness import witness
from stubs import NONE

WITNESS = witness("dynamodb_placeholder_merge")
N_SLOTS = 3

# Slot i is the placeholder pair (`#n<i>`, `:v<i>`). ESBMC numbers the
# per-iteration nondets in source order, so the caller's binding for
# slot i is `v`/`v1`/`v2`; NONE means the caller did not bind it.
CALLER = [WITNESS[f"v{i or ''}"] for i in range(N_SLOTS)]
GEN_COUNT = WITNESS["gen_count"]

BOUND = [i for i, value in enumerate(CALLER) if value != NONE]
COLLIDING = [i for i in BOUND if i < GEN_COUNT]


def _caller_names():
    return {f"#n{i}": f"attr{CALLER[i]}" for i in BOUND}


def _caller_values():
    return {f":v{i}": f"val{CALLER[i]}" for i in BOUND}


def _update_expression():
    return "SET " + ", ".join(f"#n{i} = :v{i}" for i in BOUND)


def _condition():
    """A ConditionExpression over exactly GEN_COUNT attribute names."""
    condition = Attr("cond0").eq("x0")
    for i in range(1, GEN_COUNT):
        condition = condition & Attr(f"cond{i}").eq(f"x{i}")
    return condition


def _send_update(fix=False):
    sent = []
    resource = capturing_resource("dynamodb", b"{}", sent)
    if fix:
        _apply_skip_on_collision(resource)
    resource.Table("Things").update_item(
        Key={"id": "x"},
        UpdateExpression=_update_expression(),
        ExpressionAttributeNames=_caller_names(),
        ExpressionAttributeValues=_caller_values(),
        ConditionExpression=_condition(),
    )
    return json.loads(sent[0])


def _apply_skip_on_collision(resource):
    """The proposed fix: allocate around the caller's placeholders."""
    builder = resource._injector._condition_builder
    builder._reserved_names = set()
    builder._reserved_values = set()

    def name_placeholder():
        name = f"#{builder._name_placeholder}{builder._name_count}"
        while name in builder._reserved_names:
            builder._name_count += 1
            name = f"#{builder._name_placeholder}{builder._name_count}"
        return name

    def value_placeholder():
        value = f":{builder._value_placeholder}{builder._value_count}"
        while value in builder._reserved_values:
            builder._value_count += 1
            value = f":{builder._value_placeholder}{builder._value_count}"
        return value

    builder._get_name_placeholder = name_placeholder
    builder._get_value_placeholder = value_placeholder

    def reserve(params, model, **kwargs):
        builder._reserved_names = set(params.get("ExpressionAttributeNames") or ())
        builder._reserved_values = set(params.get("ExpressionAttributeValues") or ())

    resource.meta.client.meta.events.register(
        "provide-client-params.dynamodb", reserve
    )


def test_witness_collides_with_the_generated_namespace():
    """Guard: a regenerated counterexample must still overlap."""
    assert BOUND, f"ESBMC witness {CALLER} binds no caller placeholder"
    assert GEN_COUNT >= 1, "no generated names means no merge"
    assert COLLIDING, (
        f"caller slots {BOUND} do not overlap the generated prefix "
        f"0..{GEN_COUNT - 1}"
    )


def test_generated_placeholders_replace_the_callers():
    """The bug. Fails once upstream stops overwriting on collision."""
    body = _send_update()
    names = body["ExpressionAttributeNames"]
    caller = _caller_names()

    for slot in COLLIDING:
        key = f"#n{slot}"
        assert names[key] != caller[key], (
            f"{key} survived as {names[key]!r}; expected it to be overwritten"
        )
        assert names[key] == f"cond{slot}"

    # The caller's expression still refers to these placeholders, so the
    # request names attributes the caller never supplied.
    assert body["UpdateExpression"] == _update_expression()


def test_placeholders_outside_the_generated_namespace_survive():
    """Control: the defect is the collision, not the merge itself."""
    sent = []
    resource = capturing_resource("dynamodb", b"{}", sent)
    resource.Table("Things").update_item(
        Key={"id": "x"},
        UpdateExpression="SET #colour = :red",
        ExpressionAttributeNames={"#colour": "colour"},
        ExpressionAttributeValues={":red": "red"},
        ConditionExpression=Attr("locked").eq(False),
    )
    body = json.loads(sent[0])

    assert body["ExpressionAttributeNames"]["#colour"] == "colour"
    assert body["ExpressionAttributeNames"]["#n0"] == "locked"


def test_fix_skipping_taken_suffixes_preserves_the_caller():
    """The proposed fix, on the same witness inputs."""
    body = _send_update(fix=True)
    names = body["ExpressionAttributeNames"]
    values = body["ExpressionAttributeValues"]
    caller = _caller_names()

    for key, attribute in caller.items():
        assert names[key] == attribute

    # Every generated name still made it in, at a free suffix.
    assert len(names) == len(caller) + GEN_COUNT
    assert len(values) == len(_caller_values()) + GEN_COUNT
