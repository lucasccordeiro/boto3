#!/usr/bin/env python3
"""
Empirical reproducer — candidate Finding F
  boto3/dynamodb/transform.py:165-213  TransformationInjector.inject_condition_expressions
  boto3/dynamodb/conditions.py:305-322 ConditionExpressionBuilder

  boto3 generates DynamoDB placeholder names starting at `#n0` / `:v0`
  on every request, then merges them OVER the caller's own
  `ExpressionAttributeNames` / `ExpressionAttributeValues` with
  `dict.update`. A caller who binds `#n0` in a raw expression and also
  passes a `ConditionExpression`/`KeyConditionExpression` loses that
  binding: the request is well-formed, is sent, and names an attribute
  the caller never asked for. No error, wrong data.

Source (verbatim, boto/boto3 @ 1b554d2):

  # conditions.py:313-322  ConditionExpressionBuilder
  def _get_name_placeholder(self):
      return f"#{self._name_placeholder}{self._name_count}"

  def reset(self):
      self._name_count = 0                        # <- restarts at #n0
      self._value_count = 0

  # transform.py:172  every request resets the generated namespace
  self._condition_builder.reset()

  # transform.py:203-213  the merge
  if expr_attr_names_input in params:
      params[expr_attr_names_input].update(generated_names)   # <- overwrites
  else:
      if generated_names:
          params[expr_attr_names_input] = generated_names

  if expr_attr_values_input in params:
      params[expr_attr_values_input].update(generated_values) # <- overwrites

This script drives the REAL boto3 DynamoDB resource. No request leaves
the machine: a `before-send` handler captures the serialized request and
returns a canned 200, so what is printed is the exact body boto3 would
have put on the wire.

Dependencies: boto3 (any version whose inject_condition_expressions /
ConditionExpressionBuilder bodies match the pin -- see the version note
at the bottom).
"""

import json

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.awsrequest import AWSResponse


# ── Capture the wire request without sending it ──────────────────────


class _Raw:
    def stream(self, **kwargs):
        return iter([b'{}'])


def make_table(fix=False):
    """A real dynamodb Table whose requests are captured, not sent."""
    captured = {}

    def fake_send(request, **kwargs):
        captured['body'] = json.loads(request.body)
        return AWSResponse(request.url, 200, {}, _Raw())

    resource = boto3.resource(
        'dynamodb',
        region_name='us-east-1',
        aws_access_key_id='dummy',
        aws_secret_access_key='dummy',
    )
    resource.meta.client.meta.events.register(
        'before-send.dynamodb', fake_send
    )
    if fix:
        _apply_fix(resource)
    return resource.Table('Things'), captured


# ── The proposed fix, applied to the real objects ────────────────────


def _apply_fix(resource):
    """Skip placeholder suffixes the caller has already bound.

    Patches the live `ConditionExpressionBuilder` that
    `TransformationInjector` holds (transform.py:153-155), so the
    generated names are allocated around the caller's instead of on top
    of them. This is the shape modelled by the positive control in
    ../harness/dynamodb_placeholder_merge_fixed.py.
    """
    builder = resource._injector._condition_builder
    builder._reserved_names = set()
    builder._reserved_values = set()

    def get_name_placeholder():
        name = f'#{builder._name_placeholder}{builder._name_count}'
        while name in builder._reserved_names:
            builder._name_count += 1
            name = f'#{builder._name_placeholder}{builder._name_count}'
        return name

    def get_value_placeholder():
        value = f':{builder._value_placeholder}{builder._value_count}'
        while value in builder._reserved_values:
            builder._value_count += 1
            value = f':{builder._value_placeholder}{builder._value_count}'
        return value

    builder._get_name_placeholder = get_name_placeholder
    builder._get_value_placeholder = get_value_placeholder

    def reserve(params, model, **kwargs):
        # 'provide-client-params' fires before 'before-parameter-build', so the
        # reserved sets are in place before the builder runs. Returns None:
        # a non-None response here would replace the request parameters.
        builder._reserved_names = set(params.get('ExpressionAttributeNames') or ())
        builder._reserved_values = set(params.get('ExpressionAttributeValues') or ())

    resource.meta.client.meta.events.register(
        'provide-client-params.dynamodb', reserve
    )


# ── The caller's request ─────────────────────────────────────────────
#
# "Set colour='red', but only if the row is not locked." The raw
# UpdateExpression carries the caller's own placeholders; the condition
# is written with Attr(), which is what boto3's own documentation
# recommends.

CALLER_NAMES = {'#n0': 'colour'}
CALLER_VALUES = {':v0': 'red'}


def update(fix=False, condition=True):
    table, captured = make_table(fix=fix)
    kwargs = {
        'Key': {'id': 'x'},
        'UpdateExpression': 'SET #n0 = :v0',
        'ExpressionAttributeNames': dict(CALLER_NAMES),
        'ExpressionAttributeValues': dict(CALLER_VALUES),
    }
    if condition:
        kwargs['ConditionExpression'] = Attr('locked').eq(False)
    table.update_item(**kwargs)
    return captured['body']


def show(label, body):
    print(f'  {label}')
    print(f'    UpdateExpression        : {body.get("UpdateExpression")!r}')
    print(f'    ConditionExpression     : {body.get("ConditionExpression")!r}')
    print(f'    ExpressionAttributeNames: {body.get("ExpressionAttributeNames")}')
    print(f'    ExpressionAttributeVals : {body.get("ExpressionAttributeValues")}')


print(f'boto3 {boto3.__version__}')
print()

# ── Step 1: without a ConditionExpression the binding survives ───────

print('1. Raw expression alone -- caller placeholders survive:')
baseline = update(condition=False)
show('update_item(...)', baseline)
assert baseline['ExpressionAttributeNames'] == {'#n0': 'colour'}
assert baseline['ExpressionAttributeValues'] == {':v0': {'S': 'red'}}

# ── Step 2: adding a ConditionExpression silently rebinds them ───────

print()
print('2. Same call plus ConditionExpression=Attr("locked").eq(False):')
defect = update(condition=True)
show('update_item(...)', defect)

assert defect['ExpressionAttributeNames'] == {'#n0': 'locked'}, defect
assert defect['ExpressionAttributeValues'] == {':v0': {'BOOL': False}}, defect
print()
print('    -> the caller asked to write colour=\'red\'.')
print('       The request sent writes locked=False. No error was raised.')
print('       The caller\'s own dicts are untouched (transform.py:35-36 deep-copies),')
print('       so nothing in the caller\'s process shows the substitution.')

# ── Step 3: reads are corrupted the same way ─────────────────────────

print()
print('3. The same collision on a read (query FilterExpression):')
query_table, query_capture = make_table()
query_table.query(
    KeyConditionExpression=Key('id').eq('x'),
    FilterExpression='#n0 > :v0',
    ExpressionAttributeNames={'#n0': 'score'},
    ExpressionAttributeValues={':v0': 10},
)
q = query_capture['body']
print(f'    FilterExpression        : {q.get("FilterExpression")!r}')
print(f'    KeyConditionExpression  : {q.get("KeyConditionExpression")!r}')
print(f'    ExpressionAttributeNames: {q.get("ExpressionAttributeNames")}')
print(f'    ExpressionAttributeVals : {q.get("ExpressionAttributeValues")}')
assert q['ExpressionAttributeNames'] == {'#n0': 'id'}, q
assert q['ExpressionAttributeValues'] == {':v0': {'S': 'x'}}, q
print()
print('    -> the caller filtered on score > 10.')
print('       The request filters on id > \'x\' -- a silently wrong result set.')

# ── Step 4: a non-colliding name proves the merge itself is fine ─────

print()
print('4. Control -- caller placeholders outside the #n<N> namespace:')
control_table, control_capture = make_table()
control_table.update_item(
    Key={'id': 'x'},
    UpdateExpression='SET #colour = :red',
    ExpressionAttributeNames={'#colour': 'colour'},
    ExpressionAttributeValues={':red': 'red'},
    ConditionExpression=Attr('locked').eq(False),
)
ok = control_capture['body']
show('update_item(...)', ok)
assert ok['ExpressionAttributeNames'] == {'#colour': 'colour', '#n0': 'locked'}, ok
print()
print('    -> both bindings present. The defect is the namespace collision,')
print('       not the merge.')

# ── Step 5: the fix ──────────────────────────────────────────────────

print()
print('5. With the generator skipping suffixes the caller already bound:')
fixed = update(fix=True, condition=True)
show('update_item(...)', fixed)
assert fixed['ExpressionAttributeNames'] == {'#n0': 'colour', '#n1': 'locked'}, fixed
assert fixed['ExpressionAttributeValues'] == {
    ':v0': {'S': 'red'},
    ':v1': {'BOOL': False},
}, fixed
assert fixed['ConditionExpression'] == '#n1 = :v1', fixed
assert fixed['UpdateExpression'] == 'SET #n0 = :v0', fixed
print()
print('    -> caller keeps #n0/:v0, the condition gets #n1/:v1,')
print('       and the request now writes colour=\'red\' as asked.')

print()
print('Reproduced: a raw expression combined with a ConditionExpression')
print('sends a request naming an attribute the caller never supplied.')

# Version note: this was run against the installed boto3 above. The
    # bodies of TransformationInjector.inject_condition_expressions,
# copy_dynamodb_params and ConditionExpressionBuilder.{reset,
# build_expression,_build_expression} are AST-identical between that
# release and the pin (1b554d2, boto3 1.43.75).
# `_get_name_placeholder` / `_get_value_placeholder` are the two that
# differ, and only in formatting style -- the pin builds the
# placeholder with an f-string, 1.34.46 with `'#' + ... + str(...)`.
# Same string, same namespace, same collision. Check with the AST
# comparison at the bottom of
# finding_a_collection_limit_nonpositive.py.
