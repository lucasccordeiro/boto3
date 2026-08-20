# SPDX-License-Identifier: Apache-2.0
# Positive control for candidate Finding F: the same merge with a
# generator that cannot collide with the caller's namespace.
#
# Proposed fix shape (transform.py:165-213 + conditions.py:313-322):
# hand the caller's existing `ExpressionAttributeNames` keys to the
# builder before it starts, and let `_get_name_placeholder` skip any
# suffix already taken instead of restarting unconditionally at 0:
#
#     def reset(self, reserved=()):
#         self._name_count = 0
#         self._value_count = 0
#         self._reserved = set(reserved)
#
#     def _get_name_placeholder(self):
#         name = f"#{self._name_placeholder}{self._name_count}"
#         while name in self._reserved:
#             self._name_count += 1
#             name = f"#{self._name_placeholder}{self._name_count}"
#         return name
#
# (The other admissible fix is to detect the collision at the merge and
# raise `ValueError` rather than send a request that names the wrong
# attribute. This control models the skip-on-collision fix because it
# keeps working code working -- no caller who happens to use `#n0` is
# turned from silently-wrong into broken.)
#
# The control exists to show the Finding F harness is non-vacuous:
# identical caller domain, identical generated domain, identical merge
# postcondition, collision-free allocation, Phase 1 + Phase 2
# SUCCESSFUL. If it ever regressed to FAILED, the counterexample in
# dynamodb_placeholder_merge.py would be evidence about the harness,
# not about boto3.
#
# The merged namespace has N + N slots rather than N: the real
# placeholder namespace `#n0, #n1, ...` is unbounded, so a
# skip-on-collision generator can always find a free suffix. Sizing it
# to hold every caller binding plus every generated binding models that
# without an assumption. The caller is modelled as binding only within
# `#n0..#n2`; a caller who binds a higher suffix is the same argument
# by renaming, since the fix's guard is "this slot is occupied", not
# "this slot index is low".
#
# Phase 1 + Phase 2 expected: SUCCESSFUL.

from stubs import nondet_int, __ESBMC_assume, NONE

N = 3
M = 6


def main() -> None:
    caller = [NONE, NONE, NONE]
    i = 0
    while i < N:
        v = nondet_int()
        __ESBMC_assume(v == NONE or (1 <= v and v <= 3))
        caller[i] = v
        i = i + 1

    __ESBMC_assume(caller[0] != NONE or caller[1] != NONE or caller[2] != NONE)

    gen_count = nondet_int()
    __ESBMC_assume(1 <= gen_count)
    __ESBMC_assume(gen_count <= N)

    generated = [NONE, NONE, NONE]
    j = 0
    while j < gen_count:
        w = nondet_int()
        __ESBMC_assume(4 <= w and w <= 6)
        generated[j] = w
        j = j + 1

    caller_used = 0
    c = 0
    while c < N:
        if caller[c] != NONE:
            caller_used = caller_used + 1
        c = c + 1

    # --- merge with a collision-free generator ---------------------
    merged = [NONE, NONE, NONE, NONE, NONE, NONE]
    k = 0
    while k < N:
        merged[k] = caller[k]
        k = k + 1

    placed = 0
    s = 0
    while s < M:
        if placed < gen_count and merged[s] == NONE:
            merged[s] = generated[placed]
            placed = placed + 1
        s = s + 1

    # Postcondition 1: the property the live merge violates.
    p = 0
    while p < N:
        if caller[p] != NONE:
            assert merged[p] == caller[p]
        p = p + 1

    # Postcondition 2: the fix must not lose a generated binding
    # either -- skipping a collision has to allocate elsewhere, not
    # drop the name the built ConditionExpression refers to.
    assert placed == gen_count

    # Postcondition 3: cardinality. Every binding from both sides is
    # present exactly once, which is what "merge" is supposed to mean
    # and what `dict.update` silently fails to deliver on collision.
    total = 0
    t = 0
    while t < M:
        if merged[t] != NONE:
            total = total + 1
        t = t + 1
    assert total == caller_used + gen_count


main()
