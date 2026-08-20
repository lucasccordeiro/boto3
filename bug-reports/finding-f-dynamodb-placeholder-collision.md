# Finding F — upstream issue report (DRAFT, not filed)

**Status:** drafted, **unfiled**. Filing requires explicit approval — see
[`ROADMAP.md`](../ROADMAP.md) "Upstream filing policy".

GitHub issue text for `boto/boto3`, formatted to the repository's
`bug-report.yml` template. Verified against upstream at commit `1b554d2`
(boto3 1.43.75); empirically reproduced with the script at
[`../reproducer/finding_f_dynamodb_placeholder_collision.py`](../reproducer/finding_f_dynamodb_placeholder_collision.py).

- **Title:** DynamoDB generated placeholders silently overwrite the caller's `ExpressionAttributeNames`
- **Labels:** `bug`, `needs-triage`, `dynamodb`

---

**Describe the bug**

When a DynamoDB resource call combines a **raw** expression carrying the
caller's own `ExpressionAttributeNames`/`ExpressionAttributeValues` with a
`ConditionExpression` or `KeyConditionExpression` built from `Attr(...)` /
`Key(...)`, boto3's generated placeholders **replace** the caller's bindings.
The resulting request is well-formed and is sent; it names an attribute the
caller never supplied. No exception is raised, and the caller's own dicts are
left untouched, so nothing in the caller's process reveals the substitution.

The generated namespace restarts at `#n0` / `:v0` on every request:

```python
# boto3/dynamodb/transform.py:172
self._condition_builder.reset()

# boto3/dynamodb/conditions.py:313-322
def _get_name_placeholder(self):
    return f"#{self._name_placeholder}{self._name_count}"

def reset(self):
    self._name_count = 0        # always back to #n0
    self._value_count = 0
```

and the merge lets the generated mapping win:

```python
# boto3/dynamodb/transform.py:203-213
if expr_attr_names_input in params:
    params[expr_attr_names_input].update(generated_names)     # overwrites
else:
    if generated_names:
        params[expr_attr_names_input] = generated_names

if expr_attr_values_input in params:
    params[expr_attr_values_input].update(generated_values)   # overwrites
```

`dict.update` overwrites on key collision, and `#n0` / `:v0` are ordinary
placeholder names that nothing documents as reserved.

The caller does not have to invent those names to hit this. boto3's own public
`ConditionExpressionBuilder` hands them out:

```python
>>> from boto3.dynamodb.conditions import Attr, ConditionExpressionBuilder
>>> built = ConditionExpressionBuilder().build_expression(Attr('colour').eq('red'))
>>> built.condition_expression, built.attribute_name_placeholders
('#n0 = :v0', {'#n0': 'colour'})
```

Feeding that result back into `update_item` — the documented way to build an
expression by hand — alongside an `Attr(...)` condition is enough. Note also
that the combination itself is ordinary: `update_item` has no `Attr`-based
builder for `UpdateExpression`, so a conditional update *must* mix a raw
update expression with a generated condition.

**Regression Issue**

No — the merge and the unconditional `reset()` both date back to the
introduction of the DynamoDB condition-expression transformation.

**Expected Behavior**

Either the generated placeholders are allocated around the caller's (skip a
suffix that is already bound), or the collision is rejected with a clear
error. A request that writes an attribute the caller did not name should not
be sent.

**Current Behavior**

The caller's binding is discarded and the request is sent with the generated
one. Two shapes, both silent:

*Write — the wrong attribute is written with the wrong value:*

```python
table.update_item(
    Key={'id': 'x'},
    UpdateExpression='SET #n0 = :v0',
    ExpressionAttributeNames={'#n0': 'colour'},
    ExpressionAttributeValues={':v0': 'red'},
    ConditionExpression=Attr('locked').eq(False),
)
```

serializes to

```json
{
  "UpdateExpression": "SET #n0 = :v0",
  "ConditionExpression": "#n0 = :v0",
  "ExpressionAttributeNames":  {"#n0": "locked"},
  "ExpressionAttributeValues": {":v0": {"BOOL": false}}
}
```

The caller asked to set `colour = 'red'`; the request sets `locked = false`.

*Read — the wrong rows come back:*

```python
table.query(
    KeyConditionExpression=Key('id').eq('x'),
    FilterExpression='#n0 > :v0',
    ExpressionAttributeNames={'#n0': 'score'},
    ExpressionAttributeValues={':v0': 10},
)
```

serializes with `ExpressionAttributeNames={"#n0": "id"}` and
`ExpressionAttributeValues={":v0": {"S": "x"}}`, so the caller's
`score > 10` filter is applied as `id > 'x'`.

**Reproduction Steps**

```python
import json
import boto3
from boto3.dynamodb.conditions import Attr
from botocore.awsrequest import AWSResponse


class Raw:
    def stream(self, **kwargs):
        return iter([b'{}'])


captured = {}


def capture(request, **kwargs):
    captured['body'] = json.loads(request.body)
    return AWSResponse(request.url, 200, {}, Raw())


res = boto3.resource(
    'dynamodb', region_name='us-east-1',
    aws_access_key_id='dummy', aws_secret_access_key='dummy',
)
res.meta.client.meta.events.register('before-send.dynamodb', capture)

res.Table('Things').update_item(
    Key={'id': 'x'},
    UpdateExpression='SET #n0 = :v0',
    ExpressionAttributeNames={'#n0': 'colour'},
    ExpressionAttributeValues={':v0': 'red'},
    ConditionExpression=Attr('locked').eq(False),
)
print(json.dumps(captured['body'], indent=2))
# ExpressionAttributeNames:  {'#n0': 'locked'}   expected {'#n0': 'colour'}
# ExpressionAttributeValues: {':v0': {'BOOL': False}}  expected {':v0': {'S': 'red'}}
```

Dropping the `ConditionExpression` keeps `{'#n0': 'colour'}`, and renaming the
caller's placeholder to `#colour` merges both bindings correctly — the defect
is the namespace collision, not the merge.

**Possible Solution**

Allocate generated placeholders around the caller's. Reserve the keys already
present in `params` before the builder runs, and skip a suffix that is taken:

```python
# boto3/dynamodb/conditions.py
def reset(self, reserved_names=(), reserved_values=()):
    self._name_count = 0
    self._value_count = 0
    self._reserved_names = set(reserved_names)
    self._reserved_values = set(reserved_values)

def _get_name_placeholder(self):
    name = f"#{self._name_placeholder}{self._name_count}"
    while name in self._reserved_names:
        self._name_count += 1
        name = f"#{self._name_placeholder}{self._name_count}"
    return name

# boto3/dynamodb/transform.py:172
self._condition_builder.reset(
    params.get('ExpressionAttributeNames') or (),
    params.get('ExpressionAttributeValues') or (),
)
```

With that change the example above sends `#n0 -> colour`, `#n1 -> locked`,
`ConditionExpression='#n1 = :v1'` — the caller's expression keeps working and
the condition is unchanged.

Rejecting the collision with a `ValueError` at `transform.py:203` is the other
option, but it turns working-if-lucky code into a hard failure; skipping does
not.

**Additional Information/Context**

- Affected code (commit `1b554d2`):
  - `boto3/dynamodb/transform.py:165-213` — `TransformationInjector.inject_condition_expressions`
  - `boto3/dynamodb/transform.py:172` — the unconditional `reset()`
  - `boto3/dynamodb/transform.py:203-213` — the two `dict.update` merges
  - `boto3/dynamodb/conditions.py:313-322` — placeholder generation
- The caller's own dicts are not mutated: `copy_dynamodb_params`
  (`transform.py:35-36`) deep-copies the parameters first. The corruption
  exists only in the request that is sent, which is why it cannot be caught by
  inspecting the arguments after the call.
- Mixing raw expressions with `Attr(...)`/`Key(...)` conditions is not
  documented as unsupported, and `#n<N>` is not documented as reserved — it is
  the exact namespace `ConditionExpressionBuilder` produces for callers.
- Found with [ESBMC](https://github.com/esbmc/esbmc) bounded model checking
  over the merge with a symbolic caller mapping and a symbolic generated
  prefix; the counterexample binds `#n0` on both sides. Confirmed with a
  standalone reproducer capturing the serialized request from the real
  DynamoDB resource.

**SDK version used**

1.43.75 (commit `1b554d2`); also reproduced on 1.34.46. The bodies of
`inject_condition_expressions`, `copy_dynamodb_params` and
`ConditionExpressionBuilder.{reset,build_expression,_build_expression}` are
AST-identical across the two; `_get_name_placeholder` /
`_get_value_placeholder` differ only in string-formatting style (f-string vs
concatenation) and produce the same placeholders.

**Environment details (OS name and version, etc.)**

Linux x86_64, CPython 3.12.3.
