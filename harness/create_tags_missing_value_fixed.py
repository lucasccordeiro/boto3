# SPDX-License-Identifier: Apache-2.0
# Positive control for candidate Finding G: the same loop reading the
# tag with the default EC2 already applies.
#
# Proposed fix shape (ec2/createtags.py:38):
#
#     tag_resource = self.Tag(
#         resource, tag.get('Key'), tag.get('Value', '')
#     )
#
# EC2 stores an empty value for a tag submitted without one, so
# `tag.get('Value', '')` names the tag that was actually created. This
# keeps a call the API accepts working, which validating ahead of :27
# and rejecting would not.
#
# The control exists to show the Finding G harness is non-vacuous:
# identical admitted input domain, identical ordering invariant, no
# unguarded dereference, Phase 1 + Phase 2 SUCCESSFUL. If it ever
# regressed to FAILED, the counterexample in
# create_tags_missing_value.py would be evidence about the harness, not
# about boto3.
#
# Phase 1 + Phase 2 expected: SUCCESSFUL.

from stubs import nondet_int, __ESBMC_assume

N_TAGS = 3


def main() -> None:
    has_key = [0, 0, 0]
    has_value = [0, 0, 0]
    i = 0
    while i < N_TAGS:
        k = nondet_int()
        __ESBMC_assume(0 <= k and k <= 1)
        has_key[i] = k
        v = nondet_int()
        __ESBMC_assume(0 <= v and v <= 1)
        has_value[i] = v
        i = i + 1

    n_resources = nondet_int()
    __ESBMC_assume(1 <= n_resources)
    __ESBMC_assume(n_resources <= 2)

    service_called = 1

    raised = 0
    produced = 0
    r = 0
    while r < n_resources:
        t = 0
        while t < N_TAGS:
            # `.get` on both keys: absence is a value, not a fault.
            produced = produced + 1
            t = t + 1
        r = r + 1

    assert service_called == 0 or raised == 0

    # The fix must also still return one Tag resource per (resource,
    # tag) pair -- a fix that skipped malformed tags would satisfy the
    # ordering invariant while silently returning a short list.
    assert produced == n_resources * N_TAGS


main()
