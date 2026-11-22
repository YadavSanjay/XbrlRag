# Verify failure for rounded-duplicates

Found by `tdd verify` on 2026-10-16 with the `tdd-verify` profile. Each falsifying example below goes into the locked test as `@example(...)` through a re-lock spec PR.

### test/spec/rounded-duplicates/test_rounded_duplicates.py::test_permutation_follows_the_fact

```
@pytest.mark.criterion("C2")
>   @pytest.mark.metamorphic
                   ^^^

test\spec\rounded-duplicates\test_rounded_duplicates.py:96: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

facts = [(1000000.0, -7), (2000000.0, -7)]
rnd = HypothesisRandom(generated data)

    @pytest.mark.criterion("C2")
    @pytest.mark.metamorphic
    @given(facts_st, st.randoms(use_true_random=False))
    def test_permutation_follows_the_fact(facts, rnd):
        # oracle: metamorphic — the winner is a property of the facts, not their order
        order = list(range(len(facts)))
        rnd.shuffle(order)
        permuted = [facts[i] for i in order]
        a, b = most_precise_consistent(facts), most_precise_consistent(permuted)
        assert a == _oracle(facts)
        if a is None:
            assert b is None
        else:
>           assert permuted[b] == facts[a]
E           assert (2000000.0, -7) == (1000000.0, -7)
E             
E             At index 0 diff: 2000000.0 != 1000000.0
E             Use -v to get more diff
E           Failing test case: test_permutation_follows_the_fact(
E               facts=[(1000000.0, -7), (2000000.0, -7)],
E               rnd=HypothesisRandom(generated data),
E           )
E           
E           You can reproduce this test case by temporarily adding @reproduce_failure('6.168.5', b'AAFBAUECAUECQQIAQQE=') as a decorator on your test function

test\spec\rounded-duplicates\test_rounded_duplicates.py:108: AssertionError
```

## Analysis (implementer)

The locked tests contradict each other; no implementation can pass both.

- **The tie case.** Requirement 1 says that two or more facts may share the top precision.
  A fact with int `decimals` *d* is consistent when |value − top| ≤ ½·10^(−d). Nothing
  requires the tied top facts to be *equal*. For `[(1e6, -7), (2e6, -7)]`:
  - both facts are most precise;
  - they differ by 1e6 ≤ ½·10^7, so the rule calls them consistent;
  - the result is "the first most precise fact", so index 0 here and index 1 after a swap.
- **`test_matches_rule` (C1)** follows `_oracle`, which encodes exactly that
  order-dependent first index.
- **`test_permutation_follows_the_fact` (C2)** requires the winner to be the same fact in
  every order.

So any implementation that passes C1's property test fails C2's on this input, and the
reverse.

The spec's own intent already settles it. Requirement 1 says "two most precise facts with
different values are inconsistent", but only in the sentence about `decimals=None`. It is
the right rule for every precision: two facts reported at the same precision with different
values are a real conflict, not rounding.

**Real-data impact: none.** A fact tagged with `decimals` *d* is a multiple of 10^(−d), so two
different such values differ by at least one unit, which is more than ½·10^(−d). The tie
only arises from the generator: `millions` draws multiples of 10^6 and pairs them with
`decimals` −7, −8 and −9.

## Proposed correction (re-lock on `spec/rounded-duplicates`)

1. **Requirement 1:** "Facts tied at the top precision must all have the same value;
   otherwise the facts are inconsistent." This holds for int and `None` decimals alike.
2. **`_oracle`:** return `None` when two top-precision facts differ.
3. **C1:** add the point case `[(1e6, -7), (2e6, -7)]` → `None`, and add
   `@example([(1e6, -7), (2e6, -7)], ...)` to the C2 permutation test.

No other test changes. The implementation then adds one check: the top-precision values
must be equal.

## Resolution

Accepted and re-locked on `spec/rounded-duplicates` (764081d, lock 4ab948e). Requirement 1
now says that facts tied at the top precision must be equal, and the falsifying example is a
C1 point case and an `@example` on both property tests. The implementation adds the tie
check.
