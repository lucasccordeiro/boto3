#!/usr/bin/env python3
"""
Empirical reproducer — candidate Finding G
  boto3/ec2/createtags.py:25-40  create_tags

  `ec2.create_tags(Tags=[{'Key': 'env'}])` issues the CreateTags request
  and *then* raises `KeyError: 'Value'` from an unguarded dereference at
  :38. The EC2 `Tag` shape has no required members, so botocore
  validates the call and puts it on the wire; the caller sees an
  exception for an operation that has already been issued, and a retry
  re-tags.

Source (verbatim, boto/boto3 @ 1b554d2 -- this file is byte-identical
between the pin and the installed release):

  def create_tags(self, **kwargs):
      # Call the client method
      self.meta.client.create_tags(**kwargs)              # :27  request sent
      resources = kwargs.get('Resources', [])
      tags = kwargs.get('Tags', [])
      tag_resources = []
      for resource in resources:
          for tag in tags:
              tag_resource = self.Tag(
                  resource, tag['Key'], tag['Value']      # :38  KeyError
              )
              tag_resources.append(tag_resource)
      return tag_resources

This script drives the REAL injected `create_tags` on a real EC2
service resource. No request leaves the machine: a `before-send`
handler captures the serialized request and returns a canned success,
so what is printed is the exact body boto3 put on the wire before it
raised.

Scope note: this reproduces boto3's half of the ordering -- the request
is issued, then the dereference fails. Whether the EC2 API stores a
tag whose `Value` is omitted is not established here (the response is
canned); empty tag values are legal in EC2, so the expectation is that
it does. Confirm against a live account before filing, and state the
claim as "the request is issued before the raise" if it is not.

Dependencies: boto3 (any version whose ec2/createtags.py matches the
pin -- see the version note at the bottom).
"""

import boto3
import botocore.session
from botocore.awsrequest import AWSResponse


CREATE_TAGS_OK = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<CreateTagsResponse xmlns="http://ec2.amazonaws.com/doc/2016-11-15/">'
    b'<requestId>req-1</requestId><return>true</return>'
    b'</CreateTagsResponse>'
)

RESOURCE_ID = 'i-0123456789abcdef0'


class _Raw:
    def stream(self, **kwargs):
        return iter([CREATE_TAGS_OK])


def make_ec2():
    """A real ec2 ServiceResource whose requests are captured, not sent."""
    sent = []

    def capture(request, **kwargs):
        body = request.body
        sent.append(body.decode() if isinstance(body, bytes) else body)
        return AWSResponse(request.url, 200, {}, _Raw())

    ec2 = boto3.resource(
        'ec2',
        region_name='us-east-1',
        aws_access_key_id='dummy',
        aws_secret_access_key='dummy',
    )
    ec2.meta.client.meta.events.register('before-send.ec2', capture)
    return ec2, sent


def create_tags_fixed(self, **kwargs):
    """ec2/createtags.py:25-40 with `.get` in place of `[...]` at :38."""
    self.meta.client.create_tags(**kwargs)
    resources = kwargs.get('Resources', [])
    tags = kwargs.get('Tags', [])
    tag_resources = []
    for resource in resources:
        for tag in tags:
            tag_resources.append(
                self.Tag(resource, tag.get('Key'), tag.get('Value', ''))
            )
    return tag_resources


print(f'boto3 {boto3.__version__}')
print()

# ── Step 1: botocore accepts a tag with no Value ─────────────────────

tag_shape = botocore.session.get_session().get_service_model('ec2').shape_for('Tag')
print('1. The EC2 `Tag` shape, from botocore\'s service model:')
print(f'     members : {list(tag_shape.members)}')
print(f'     required: {tag_shape.required_members}')
assert tag_shape.required_members == [], tag_shape.required_members
print('   -> nothing is required, so validation cannot reject a partial tag.')

# ── Step 2: a well-formed call works ─────────────────────────────────

print()
print('2. A complete tag -- the documented happy path:')
happy_ec2, happy_sent = make_ec2()
got = happy_ec2.create_tags(
    Resources=[RESOURCE_ID], Tags=[{'Key': 'env', 'Value': 'prod'}]
)
print(f'     returned: {got}')
print(f'     requests sent: {len(happy_sent)}')
assert len(got) == 1 and len(happy_sent) == 1

# ── Step 3: omitting Value raises AFTER the request is issued ────────

print()
print('3. The same call with `Value` omitted:')
ec2, sent = make_ec2()
raised = None
try:
    ec2.create_tags(Resources=[RESOURCE_ID], Tags=[{'Key': 'env'}])
except KeyError as exc:
    raised = exc

assert raised is not None, 'expected KeyError from createtags.py:38'
print(f'     raised: KeyError({raised})')
print(f'     requests sent before the raise: {len(sent)}')
print(f'     body: {sent[0]}')
assert len(sent) == 1, 'the request must already have been issued'
assert 'Tag.1.Key=env' in sent[0]
assert 'Tag.1.Value' not in sent[0]
print()
print('     -> CreateTags was issued with Tag.1.Key=env and no value,')
print('        and only then did boto3 raise. The caller cannot tell')
print('        whether the tags were applied; retrying re-tags.')

# ── Step 4: `Key` is unguarded on the same line ──────────────────────

print()
print('4. The same line dereferences `Key` unguarded too:')
ec2, sent = make_ec2()
raised = None
try:
    ec2.create_tags(Resources=[RESOURCE_ID], Tags=[{'Value': 'prod'}])
except KeyError as exc:
    raised = exc
assert raised is not None
print(f'     Tags=[{{\'Value\': \'prod\'}}] -> KeyError({raised}), '
      f'after {len(sent)} request(s)')

# ── Step 5: the fix ──────────────────────────────────────────────────

print()
print('5. With `.get(\'Value\', \'\')` at :38 -- the value EC2 itself stores:')
ec2, sent = make_ec2()
got = create_tags_fixed(ec2, Resources=[RESOURCE_ID], Tags=[{'Key': 'env'}])
print(f'     returned: {got}')
print(f'     tag.value: {got[0].value!r}')
assert len(got) == 1
assert got[0].key == 'env'
assert got[0].value == ''
print()
print('     -> a Tag resource naming the tag that was created, and no')
print('        exception for a request the API accepted.')

print()
print('Reproduced: create_tags raises KeyError after issuing the request.')

# Version note: this was run against the installed boto3 above.
# boto3/ec2/createtags.py is byte-identical between that release and the
# pin (1b554d2, boto3 1.43.75) -- diffed directly, not just AST-compared
# -- so the observation carries to the pinned source.
