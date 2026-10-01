"""A settings file is read on the way to spawning a keyboard, so no document may raise."""

import tomllib

from hypothesis import given
from hypothesis import strategies as st

from yoga_deck.core.settings import (
    MAX_FONT_LENGTH,
    MAX_OSK_HEIGHT,
    MIN_OSK_HEIGHT,
    SECTIONS,
    SETTING_NAMES,
    SPECS_BY_NAME,
    OskSettings,
    Settings,
    parse_settings,
    render_document,
    resolve_layers,
)

# Everything tomllib can hand back, plus the shapes a hand-written file gets wrong.
scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=12),
)
values = st.recursive(
    scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=3), st.dictionaries(st.text(max_size=8), children, max_size=3)
    ),
    max_leaves=6,
)

known_keys = st.sampled_from(
    [*OskSettings.__dataclass_fields__, *Settings().pen.__dataclass_fields__]
)
sections = st.dictionaries(st.one_of(known_keys, st.text(max_size=8)), values, max_size=6)
documents = st.dictionaries(
    st.one_of(st.sampled_from(SECTIONS), st.text(max_size=8)),
    st.one_of(sections, values),
    max_size=4,
)


@given(documents)
def test_no_settings_document_can_raise(document) -> None:
    parsed = parse_settings(document)

    assert isinstance(parsed.settings, Settings)
    assert all(isinstance(name, str) for name in parsed.rejected)
    # Sorted, so a diagnostic never depends on the order keys happened to appear in the file.
    assert list(parsed.rejected) == sorted(parsed.rejected)


@given(documents)
def test_every_document_renders_a_usable_wvkbd_argument_vector(document) -> None:
    settings = parse_settings(document).settings.osk
    args = settings.to_args()

    assert args[:1] == ("-L",)
    assert args[2:3] == ("-H",)
    assert args[4:5] == ("--fn",)
    assert MIN_OSK_HEIGHT <= int(args[1]) <= MAX_OSK_HEIGHT
    assert MIN_OSK_HEIGHT <= int(args[3]) <= MAX_OSK_HEIGHT
    assert args[5].strip() == args[5] and args[5]
    assert len(args[5]) <= MAX_FONT_LENGTH
    assert all(ord(character) >= 0x20 and ord(character) != 0x7F for character in args[5])
    # Every remaining entry is a flag or the value of one; none may be empty or whitespace.
    assert all(entry and entry.strip() == entry for entry in args)


@given(documents)
def test_rejecting_a_key_never_changes_another_one(document) -> None:
    """Per-key degradation: what survives must equal what a file holding only it would give."""

    parsed = parse_settings(document)

    for section in SECTIONS:
        resolved = getattr(parsed.settings, section)
        default = getattr(Settings(), section)
        for name in type(resolved).__dataclass_fields__:
            value = getattr(resolved, name)
            if value == getattr(default, name):
                continue
            alone = parse_settings({section: {name: document[section][name]}})
            assert getattr(getattr(alone.settings, section), name) == value


# --- The CLI and the loader must agree, for any text a person can type ---


@given(name=st.sampled_from(SETTING_NAMES), text=st.text(max_size=40))
def test_the_prompt_can_never_store_something_the_loader_would_reject(name: str, text: str) -> None:
    """The one invariant tying `config set` to the settings file.

    `parse_text` is the only path by which a typed value becomes a stored one. If it could
    accept text that `validate` then refuses, `config set` would report success and the
    keyboard would silently keep the old value.
    """

    spec = SPECS_BY_NAME[name]
    parsed = spec.parse_text(text)

    if parsed is not None:
        assert spec.validate(spec.to_toml(parsed)) == parsed


def _typed_text(name: str) -> st.SearchStrategy[str]:
    """Text a person plausibly types here, so the round trip is not mostly rejections."""

    spec = SPECS_BY_NAME[name]
    if spec.kind == "integer":
        return st.integers(min_value=spec.minimum, max_value=spec.maximum).map(str)
    if spec.kind == "flag":
        return st.sampled_from(("true", "false", "TRUE", "False"))
    if spec.kind == "choice":
        return st.sampled_from(spec.choices)
    # No spaces or control characters, so stripping can never empty the string.
    return st.text(
        alphabet=st.characters(
            min_codepoint=33, max_codepoint=0x2FFF, blacklist_categories=("Cc",)
        ),
        min_size=1,
        max_size=20,
    )


@given(
    st.sampled_from(SETTING_NAMES).flatmap(lambda name: st.tuples(st.just(name), _typed_text(name)))
)
def test_whatever_the_prompt_accepts_survives_being_written_and_read_back(
    typed: tuple[str, str],
) -> None:
    name, text = typed
    spec = SPECS_BY_NAME[name]
    parsed = spec.parse_text(text)
    assert parsed is not None

    body = render_document({spec.section: {spec.field: spec.to_toml(parsed)}})
    settings = resolve_layers((("stored", tomllib.loads(body)),)).settings

    assert getattr(getattr(settings, spec.section), spec.field) == parsed


@given(
    documents=st.lists(
        st.dictionaries(
            st.sampled_from((*SECTIONS, "junk")),
            st.dictionaries(st.text(max_size=12), scalars, max_size=4),
            max_size=3,
        ),
        max_size=4,
    )
)
def test_no_stack_of_documents_can_raise_or_produce_an_unusable_keyboard(
    documents: list[dict],
) -> None:
    parsed = resolve_layers(
        (f"layer-{index}", document) for index, document in enumerate(documents)
    )

    args = parsed.settings.osk.to_args()
    assert MIN_OSK_HEIGHT <= int(args[args.index("-L") + 1]) <= MAX_OSK_HEIGHT
    assert all(entry.strip() for entry in args)
    # Every name that was kept has an origin, and every name that was lost names its file.
    assert {name for name, _ in parsed.origins} <= set(SETTING_NAMES)
    assert {name for _, name in parsed.problems} == set(parsed.rejected)


@given(
    lower=st.integers(min_value=MIN_OSK_HEIGHT, max_value=MAX_OSK_HEIGHT),
    upper=scalars,
)
def test_a_higher_layer_can_only_ever_lose_its_own_key(lower: int, upper: object) -> None:
    """Per-layer validation, stated as a property: a bad override costs one key, not the stack."""

    stacked = resolve_layers(
        (
            ("lower", {"osk": {"landscape_height": lower}}),
            ("upper", {"osk": {"landscape_height": upper}}),
        )
    )

    accepted = SPECS_BY_NAME["osk.landscape_height"].validate(upper)
    assert stacked.settings.osk.landscape_height == (lower if accepted is None else accepted)
