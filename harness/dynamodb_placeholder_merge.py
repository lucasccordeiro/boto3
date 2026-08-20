# SPDX-License-Identifier: Apache-2.0
# Target: live-bug witness for the DynamoDB placeholder merge.
# Candidate Finding F (see ROADMAP.md Tier 4 row 1).
#
# Trace against pinned boto/boto3 @ 1b554d2:
#
#   1. `TransformationInjector.inject_condition_expressions`
#      (transform.py:165-213) resets the builder at :172, so every
#      request restarts the generated namespace at `#n0` / `:v0`
#      (conditions.py:313-322: `_name_count = 0`, placeholder is
#      f"#{self._name_placeholder}{self._name_count}").
#
#   2. Building the caller's `ConditionExpression=Attr(...)` fills
#      `generated_names` with `#n0 .. #n(g-1)` for the g names the
#      condition mentions.
#
#   3. The merge at :203-204 lets the generated mapping win:
#          if expr_attr_names_input in params:
#              params[expr_attr_names_input].update(generated_names)
#      `dict.update` overwrites on key collision. A caller who supplied
#      their own `ExpressionAttributeNames={'#n0': ...}` for a *raw*
#      UpdateExpression/FilterExpression therefore loses that binding,
#      with no error, after the parameters have already validated.
#
# Net effect: the request is well-formed and is sent, but names the
# wrong attribute. `:v0` collides identically via the values merge at
# :209-210.
#
# Harness shape: the placeholder namespace is the ordered set of names
# `#n0, #n1, ...`, so a mapping over it is modelled as a list indexed
# by the integer suffix; a binding is the attribute name it points at,
# drawn from disjoint domains (caller 1..3, generated 4..6) so an
# overwrite is observable. `gen_count` is symbolic because the number
# of generated names depends on the condition the caller wrote; the
# generated names always occupy the *prefix* 0..gen_count-1, which is
# exactly what `reset()` guarantees.
#
# Everything is inlined into `main` -- lists lose their elements' type
# tags when passed as function arguments under ESBMC 8.4.0 (constraint
# C1, ../reproducer/esbmc_list_parameter_type_tag_loss.py).
#
# Phase 1 expected: FAILED (the counterexample IS the bug report).
# Phase 2: skipped.

from stubs import nondet_int, __ESBMC_assume, NONE

N = 3


def main() -> None:
    # --- the caller's own ExpressionAttributeNames -----------------
    # NONE marks a placeholder the caller did not bind. A caller who
    # writes a raw expression binds whichever suffixes they chose;
    # `#n<j>` is a legal choice and nothing documents it as reserved.
    caller = [NONE, NONE, NONE]
    i = 0
    while i < N:
        v = nondet_int()
        __ESBMC_assume(v == NONE or (1 <= v and v <= 3))
        caller[i] = v
        i = i + 1

    # At least one caller binding, else there is nothing to preserve
    # and the property holds trivially.
    __ESBMC_assume(caller[0] != NONE or caller[1] != NONE or caller[2] != NONE)

    # --- generated_names, conditions.py:313-322 --------------------
    # After reset() the generator hands out `#n0` upward with no gaps,
    # one per name in the caller's ConditionExpression.
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

    # --- the merge, transform.py:203-204 ---------------------------
    #     params['ExpressionAttributeNames'].update(generated_names)
    merged = [NONE, NONE, NONE]
    k = 0
    while k < N:
        merged[k] = caller[k]
        k = k + 1
    m = 0
    while m < N:
        if generated[m] != NONE:
            merged[m] = generated[m]        # :204 dict.update overwrites
        m = m + 1

    # Postcondition: merging boto3's generated placeholders into the
    # caller's mapping must not silently drop a caller binding. The
    # caller's expression string still refers to `#n<p>`, so a changed
    # binding means the request names an attribute the caller never
    # asked for.
    p = 0
    while p < N:
        if caller[p] != NONE:
            assert merged[p] == caller[p]
        p = p + 1


main()
