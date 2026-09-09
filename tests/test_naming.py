"""Tests for the standalone podcast name generator."""

import random

from app.naming import (
    ADJECTIVES,
    ANIMALS,
    OBJECTS,
    PLACES,
    SCIENCE,
    generate_name,
)

_ALL_POOLS = {
    "ADJECTIVES": ADJECTIVES,
    "ANIMALS": ANIMALS,
    "OBJECTS": OBJECTS,
    "SCIENCE": SCIENCE,
    "PLACES": PLACES,
}


def test_pools_non_empty():
    for name, pool in _ALL_POOLS.items():
        assert len(pool) > 0, name


def test_word_pools_are_lowercase():
    for name in ("ADJECTIVES", "ANIMALS", "OBJECTS", "SCIENCE"):
        pool = _ALL_POOLS[name]
        for entry in pool:
            assert entry == entry.lower(), f"{name} entry not lowercase: {entry!r}"


def test_places_are_title_case():
    for entry in PLACES:
        assert entry == entry.title(), f"PLACES entry not Title Case: {entry!r}"


def test_no_duplicate_entries_within_any_pool():
    for name, pool in _ALL_POOLS.items():
        assert len(set(pool)) == len(pool), f"duplicate entries in {name}"


def test_generated_names_are_title_case_two_words():
    rng = random.Random(1234)
    for _ in range(200):
        name = generate_name(rng=rng)
        assert name == name.title()
        assert name[0].isupper()
        assert len(name.split()) >= 2


def test_deterministic_with_seeded_rng():
    rng_a = random.Random(42)
    a = [generate_name(rng=rng_a) for _ in range(50)]
    rng_b = random.Random(42)
    b = [generate_name(rng=rng_b) for _ in range(50)]
    assert a == b


def test_collision_retry_generates_different_name():
    seed = 99
    blocked = generate_name(rng=random.Random(seed))
    regenerated = generate_name(
        existing_names={blocked}, rng=random.Random(seed)
    )
    assert regenerated != blocked


def test_weight_distribution_within_tolerance():
    place_last = {p.split()[-1] for p in PLACES}
    animal_titles = {a.title() for a in ANIMALS}
    object_last = {o.split()[-1].title() for o in OBJECTS}

    rng = random.Random(7)
    counts = {1: 0, 2: 0, 3: 0}
    for _ in range(5000):
        name = generate_name(rng=rng)
        last = name.split()[-1]
        if last in place_last:
            pattern = 3
        elif last in animal_titles:
            pattern = 1
        elif last in object_last:
            pattern = 2
        else:
            raise AssertionError(f"unclassified name: {name!r}")
        counts[pattern] += 1

    total = sum(counts.values())
    for pattern, expected in ((1, 60), (2, 25), (3, 15)):
        share = counts[pattern] / total * 100
        assert abs(share - expected) <= 5, (
            f"pattern {pattern} share {share:.1f}% outside ±5 of {expected}%"
        )


class _AlwaysFirstRng:
    def random(self):
        return 0.0

    def choice(self, seq):
        return seq[0]


def test_safety_valve_raises_after_200_attempts():
    blocked = f"{ADJECTIVES[0]} {ANIMALS[0]}".title()
    try:
        generate_name(existing_names={blocked}, rng=_AlwaysFirstRng())
    except RuntimeError as exc:
        assert str(exc) == "could not generate a unique podcast name"
    else:
        raise AssertionError("expected RuntimeError for unresolvable collisions")