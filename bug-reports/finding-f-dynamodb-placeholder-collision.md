# Finding F — filed as boto/boto3#4831

**Status: FILED 2026-08-20 as [boto/boto3#4831](https://github.com/boto/boto3/issues/4831)** (open, labelled `bug`,
`needs-triage`). The text below is what was submitted, kept here as the record;
edit it only to keep it in step with the issue.

Every `file:line` below was checked against `boto/boto3` `develop` at commit
`ced31bb7` on 2026-08-20 — the merge at `transform.py:203-204` and the
unconditional `reset()` at `:172` are both still present. Behaviour was
reproduced on boto3/botocore 1.34.46.

**Prior art:** [#4032](https://github.com/boto/boto3/issues/4032) reported the
same defect from the values side in Feb 2024 and was **closed in June 2024 with
a workaround** ("write the condition as a raw string"); the root cause was never
identified. Lead with their reproduction — it uses ordinary placeholder names
and shows real data corruption — so this does not get closed the same way.

The reproduction was re-run against the filed text on boto3 1.34.46 and 1.43.76
after submission; both print `{':v1': 'bar', ':v0': 'foo'}`.

---

## Title

```
DynamoDB: Attr-generated placeholders overwrite the caller's ExpressionAttributeValues
```

## Describe the bug

`ConditionExpressionBuilder` restarts at `#n0` / `:v0` on every request
(`boto3/dynamodb/transform.py:172`), and the generated placeholders are merged
**over** the caller's with `dict.update`
(`boto3/dynamodb/transform.py:203-213`):

```python
if expr_attr_names_input in params:
    params[expr_attr_names_input].update(generated_names)
```

Any caller placeholder named `#n<k>` or `:v<k>` with `k` below the generated
count is silently replaced. The request stays well-formed and is sent, writing
data the caller never supplied. No exception is raised.

This is the root cause of #4032, which was closed with a workaround. The naming
required is not unusual — `:v1` is enough.

## Regression Issue

No. Present since the condition-expression transformation was introduced, and
still on `develop` at `ced31bb7`.

## Expected Behavior

Caller-supplied placeholders survive the merge, and generated ones take
suffixes that are free. Failing that, an explicit error. A request must not
write a value the caller did not supply.

## Current Behavior

From #4032: `val1` is set to `"bar"` — a condition operand — instead of `321`.

```
caller    ExpressionAttributeValues  {':v1': 321}
generated                            {':v0': 'foo', ':v1': 'bar'}   # is_in makes two
sent                                 {':v1': 'bar', ':v0': 'foo'}   # caller's :v1 gone
          UpdateExpression 'SET #f1=:v1'  ->  writes 'bar'
```

Names collide the same way via `#n0`, and reads are affected too: a `query`
whose `FilterExpression='#n0 > :v0'` binds `score` / `10`, combined with
`KeyConditionExpression=Key('id').eq('x')`, is sent filtering `id > 'x'`.

The caller's own dictionaries are deep-copied first
(`boto3/dynamodb/transform.py:35-36`), so nothing in the caller's process shows
the substitution.

## Reproduction Steps

No AWS account or network required:

```python
import botocore.session
from boto3.dynamodb.conditions import Attr
from boto3.dynamodb.transform import TransformationInjector

model = botocore.session.get_session().get_service_model(
    'dynamodb').operation_model('UpdateItem')

params = {                                   # verbatim from #4032
    'TableName': 'test_table',
    'Key': {'test_key': 'foo-key'},
    'UpdateExpression': 'SET #f1=:v1',
    'ExpressionAttributeNames': {'#f1': 'val1'},
    'ExpressionAttributeValues': {':v1': 321},
    'ConditionExpression': Attr('filter_val').is_in(['foo', 'bar']),
}

TransformationInjector().inject_condition_expressions(params, model)
print(params['ExpressionAttributeValues'])
# {':v1': 'bar', ':v0': 'foo'}   -- the caller's :v1 = 321 is gone
```

The same arguments passed to `table.update_item(...)` against a real table
reproduce it end to end, as reported in #4032.

## Possible Solution

Reserve the caller's keys before the builder runs, and skip a taken suffix:

```python
# boto3/dynamodb/conditions.py
def reset(self, reserved_names=(), reserved_values=()):
    self._name_count = 0
    self._value_count = 0
    self._reserved_names = set(reserved_names)
    self._reserved_values = set(reserved_values)

def _get_value_placeholder(self):
    value = f":{self._value_placeholder}{self._value_count}"
    while value in self._reserved_values:
        self._value_count += 1
        value = f":{self._value_placeholder}{self._value_count}"
    return value          # same shape for _get_name_placeholder

# boto3/dynamodb/transform.py:172
self._condition_builder.reset(
    params.get('ExpressionAttributeNames') or (),
    params.get('ExpressionAttributeValues') or (),
)
```

Rejecting the collision with an error is the alternative, but it breaks callers
that work today by luck; skipping does not.

## Additional Information/Context

- Affected code: `boto3/dynamodb/transform.py:165-213` (`:172` reset,
  `:203-213` the two merges) and `boto3/dynamodb/conditions.py:313-322`
  (placeholder generation).
- `update_item` has no `Attr`-based builder for `UpdateExpression`, so mixing a
  raw update expression with a generated condition is the normal shape for a
  conditional update — not an unusual usage.
- Related: #4032, closed with a workaround.
- Found with [ESBMC](https://github.com/esbmc/esbmc) bounded model checking over
  the merge; the counterexample collides on the first generated placeholder.

## SDK version used

```
Reproduced on boto3 1.34.46 / botocore 1.34.46; code path unchanged on develop at ced31bb7.
```

## Environment details (OS name and version, etc.)

```
Ubuntu 24.04.4 LTS, x86_64, CPython 3.12.3
```
