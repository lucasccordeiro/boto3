# SPDX-License-Identifier: Apache-2.0
# Target: live-bug witness for EC2 `create_tags` with a tag that omits
# `Value`. Candidate Finding G (see ROADMAP.md Tier 3 row 1).
#
# Source (verbatim, boto/boto3 @ 1b554d2, ec2/createtags.py:25-40):
#
#     def create_tags(self, **kwargs):
#         # Call the client method
#         self.meta.client.create_tags(**kwargs)          # :27
#         resources = kwargs.get('Resources', [])
#         tags = kwargs.get('Tags', [])
#         tag_resources = []
#         for resource in resources:                      # :34
#             for tag in tags:                            # :35
#                 tag_resource = self.Tag(
#                     resource, tag['Key'], tag['Value']  # :38
#                 )
#                 tag_resources.append(tag_resource)
#         return tag_resources
#
# The EC2 `Tag` shape has **no required members** (botocore
# `service-2.json`; `shape_for('Tag').required_members == []`), so
# `Tags=[{'Key': 'env'}]` passes botocore validation and the request is
# put on the wire. Only afterwards does :38 dereference `tag['Value']`
# unguarded, raising `KeyError: 'Value'` at the caller.
#
# The ordering is the whole point: the mutation at :27 has already been
# requested when the dereference at :38 fails. The caller sees an
# exception for an operation that was issued, and a retry re-tags.
#
# Modelling: dict membership is a 0/1 flag per key (constraint C3), and
# the escaping exception is a monotone `raised` flag. The flag lists are
# annotated `list[int]`; a bare `list` would lose the element type in
# the callee (esbmc/esbmc#7187, constraint C1). The property is
# the ordering invariant -- not "the input is well-formed", which is
# precisely what boto3 never checks.
#
# Phase 1 expected: FAILED (the counterexample IS the bug report).
# Phase 2: skipped.

from stubs import nondet_int, __ESBMC_assume

N_TAGS = 3


def build_tag_resources(
    has_key: list[int], has_value: list[int], n_tags: int, n_resources: int
) -> int:
    """createtags.py:34-39. Returns 1 if the loop raises, 0 otherwise."""
    raised = 0
    r = 0
    while r < n_resources:
        t = 0
        while t < n_tags:
            if has_key[t] == 0:
                raised = 1                  # :38 tag['Key']
            if has_value[t] == 0:
                raised = 1                  # :38 tag['Value']
            t = t + 1
        r = r + 1
    return raised


def main() -> None:
    # botocore admits any subset of {Key, Value} per tag: the shape has
    # no required members, so validation cannot reject a partial tag.
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

    # --- :27 the service call ---------------------------------------
    # Validation has passed and the CreateTags request has been issued.
    service_called = 1

    raised = build_tag_resources(has_key, has_value, N_TAGS, n_resources)

    # Postcondition: no exception escapes `create_tags` once the
    # request at :27 has been issued. Equivalently: whatever validation
    # this method needs belongs before the service call, not after it.
    assert service_called == 0 or raised == 0


main()
