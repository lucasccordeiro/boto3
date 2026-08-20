# SPDX-License-Identifier: Apache-2.0
"""Drive a real boto3 resource without letting a request leave the machine.

A `before-send` handler records the serialized request and returns a
canned success, so the code under test is boto3's own all the way down
to the wire format.

The reproducers under `reproducer/` carry their own copy of this on
purpose: they are standalone artefacts meant to be pasted into an issue.
"""

from __future__ import annotations

import boto3
from botocore.awsrequest import AWSResponse


class _CannedBody:
    def __init__(self, payload: bytes):
        self._payload = payload

    def stream(self, **kwargs):
        return iter([self._payload])


def capturing_resource(service: str, payload: bytes, sent: list):
    """A real service resource whose requests are captured, not sent."""

    def before_send(request, **kwargs):
        body = request.body
        sent.append(body.decode() if isinstance(body, bytes) else body)
        return AWSResponse(request.url, 200, {}, _CannedBody(payload))

    resource = boto3.resource(
        service,
        region_name="us-east-1",
        aws_access_key_id="dummy",
        aws_secret_access_key="dummy",
    )
    resource.meta.client.meta.events.register(f"before-send.{service}", before_send)
    return resource
