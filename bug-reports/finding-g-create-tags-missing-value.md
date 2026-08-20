# Finding G — upstream issue report (DRAFT, not filed)

**Status:** drafted, **unfiled**. Filing requires explicit approval — see
[`ROADMAP.md`](../ROADMAP.md) "Upstream filing policy".

**Before filing:** confirm against a live account that EC2 stores a tag
submitted without `Value` (the reproducer uses a canned response, so it
establishes boto3's ordering — request issued, then raise — but not the
server's acceptance). If EC2 rejects the call, the finding still stands as an
unguarded dereference raising a raw `KeyError`, but the "succeeded then raised"
framing below must be dropped.

GitHub issue text for `boto/boto3`, formatted to the repository's
`bug-report.yml` template. Verified against upstream at commit `1b554d2`
(boto3 1.43.75); empirically reproduced with the script at
[`../reproducer/finding_g_create_tags_missing_value.py`](../reproducer/finding_g_create_tags_missing_value.py).

- **Title:** `ec2.create_tags` raises `KeyError` after the request has been sent
- **Labels:** `bug`, `needs-triage`, `ec2`

---

**Describe the bug**

`ec2.create_tags(Tags=[{'Key': 'env'}])` issues the `CreateTags` request and
*then* raises `KeyError: 'Value'` from an unguarded dereference. The caller
gets a raw `KeyError` for an operation that has already gone to the service,
cannot tell whether the tags were applied, and re-tags if it retries.

The EC2 `Tag` shape has no required members:

```python
>>> botocore.session.get_session().get_service_model('ec2').shape_for('Tag').required_members
[]
```

so botocore validates the call and serializes it. Only afterwards does boto3
index the tag:

```python
# boto3/ec2/createtags.py:25-40
def create_tags(self, **kwargs):
    # Call the client method
    self.meta.client.create_tags(**kwargs)          # request sent here
    resources = kwargs.get('Resources', [])
    tags = kwargs.get('Tags', [])
    tag_resources = []
    for resource in resources:
        for tag in tags:
            tag_resource = self.Tag(
                resource, tag['Key'], tag['Value']  # :38  KeyError here
            )
            tag_resources.append(tag_resource)
    return tag_resources
```

Both subscripts are unguarded; `Tags=[{'Value': 'prod'}]` raises
`KeyError: 'Key'` the same way.

**Regression Issue**

No — `create_tags` has had this shape since it was introduced.

**Expected Behavior**

Either the tag resources are built from what was actually submitted, or the
input is rejected *before* the service call. What should not happen is an
exception raised after the mutation has been requested.

**Current Behavior**

The `CreateTags` request is put on the wire, and `create_tags` then raises
`KeyError: 'Value'`. The captured request body, taken from a `before-send`
handler immediately before the raise:

```
Action=CreateTags&Version=2016-11-15&ResourceId.1=i-0123456789abcdef0&Tag.1.Key=env
```

**Reproduction Steps**

```python
import boto3
from botocore.awsrequest import AWSResponse

OK = (b'<?xml version="1.0" encoding="UTF-8"?>'
      b'<CreateTagsResponse xmlns="http://ec2.amazonaws.com/doc/2016-11-15/">'
      b'<requestId>r-1</requestId><return>true</return></CreateTagsResponse>')


class Raw:
    def stream(self, **kwargs):
        return iter([OK])


sent = []


def capture(request, **kwargs):
    sent.append(request.body)
    return AWSResponse(request.url, 200, {}, Raw())


ec2 = boto3.resource(
    'ec2', region_name='us-east-1',
    aws_access_key_id='dummy', aws_secret_access_key='dummy',
)
ec2.meta.client.meta.events.register('before-send.ec2', capture)

try:
    ec2.create_tags(Resources=['i-0123456789abcdef0'], Tags=[{'Key': 'env'}])
except KeyError as exc:
    print('raised KeyError:', exc)          # 'Value'
print('requests already sent:', len(sent))  # 1
print('body:', sent[0])                     # ...&Tag.1.Key=env
```

Against a live account, drop the `before-send` handler and check the instance's
tags afterwards: the tag is present with an empty value, and the call raised.

**Possible Solution**

Read the tag with the default EC2 itself applies — an omitted `Value` is stored
as an empty string:

```python
# boto3/ec2/createtags.py:38
tag_resource = self.Tag(resource, tag.get('Key'), tag.get('Value', ''))
```

This keeps a call the API accepts working. Validating ahead of the service call
and raising a clean error is the alternative, but it would reject a request EC2
handles, so it is a behaviour change rather than a fix.

**Additional Information/Context**

- Affected code (commit `1b554d2`): `boto3/ec2/createtags.py:25-40`, with the
  service call at `:27` and the unguarded dereference at `:38`.
- The ordering is what makes this more than an input-validation nit: any
  validation this method needs belongs before `:27`, not after it.
- Found with [ESBMC](https://github.com/esbmc/esbmc) bounded model checking
  over the loop with symbolic key-presence flags; the property is
  "no exception escapes once the request at `:27` has been issued", and the
  counterexample is a tag carrying `Key` but not `Value`. Confirmed with a
  standalone reproducer capturing the serialized request from the real EC2
  service resource.

**SDK version used**

1.43.75 (commit `1b554d2`); also reproduced on 1.34.46 —
`boto3/ec2/createtags.py` is byte-identical between the two.

**Environment details (OS name and version, etc.)**

Linux x86_64, CPython 3.12.3.
