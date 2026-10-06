from __future__ import annotations

import pytest

from assist_decider_server.pipeline import MAX_OPTIONS, decide
from assist_decider_server.protocol import Entity, Home
from assist_decider_server.providers import Answer

from .conftest import ALL_INTENTS, HOME, FakeProvider, intent_rule, make_request


def run(text: str, rule=None, language: str = "en", p: float = 0.95, **kw):
    provider = FakeProvider(rule, p=p)
    response, trace = decide(make_request(text, language, **kw), provider)
    return response, trace, provider


def decide_with(provider, text: str, language: str = "en", **kw):
    response, trace = decide(make_request(text, language, **kw), provider)
    return response, trace, provider


def acts(response):
    return [(a.intent, a.slots) for a in response.actions]


def test_exact_entity_name_skips_target_question():
    response, trace, provider = run("turn on the kitchen light", intent_rule("HassTurnOn"))
    assert acts(response) == [("HassTurnOn", {"name": "light.kitchen_ceiling"})]
    assert [c[0] for c in provider.calls] == ["intent"]
    assert trace["segments"][0]["shortcut"] == "spoken names"


def test_brightness_with_number():
    response, *_ = run("set the kitchen light to 50%", intent_rule("HassLightSet"))
    assert acts(response) == [("HassLightSet", {"name": "light.kitchen_ceiling", "brightness": 50})]


def test_area_plus_domain_word():
    response, *_ = run("turn off the lights in the hallway", intent_rule("HassTurnOff"))
    assert acts(response) == [("HassTurnOff", {"area": "hallway", "domain": ["light"]})]


def test_area_without_domain_escalates():
    response, *_ = run("turn off the bedroom", intent_rule("HassTurnOff"))
    assert response.status == "escalate"
    assert response.reason == "area_without_domain"


def test_implied_domain_for_temperature_in_area():
    response, *_ = run("set the bathroom to 22 degrees", intent_rule("HassClimateSetTemperature"))
    # Climate handlers in HA take no "domain" slot.
    assert acts(response) == [
        ("HassClimateSetTemperature", {"area": "bathroom", "temperature": 22.0})
    ]


def masked(trace, n=0):
    return set(trace["segments"][0]["questions"][n]["masked"])


def test_model_always_sees_every_intent_but_guards_mask_answers():
    _, trace, provider = run("turn on the kitchen light", intent_rule("HassTurnOn"))
    assert len(provider.calls[0][1].options) == len(ALL_INTENTS) + 1
    assert {"set_brightness", "set_position", "set_temperature"} <= masked(trace)
    assert trace["segments"][0]["dropped_intents"]["HassLightSet"] == "no value spoken"


def test_off_word_masks_turn_on():
    # The model prefers "turn_on"; the spoken "aus" rules it out.
    response, trace, _ = run(
        "Mach das Licht in der Küche aus", intent_rule("HassTurnOn"), "de", p=0.6
    )
    assert "turn_on" in masked(trace)
    assert response.status == "escalate" or acts(response)[0][0] != "HassTurnOn"


def test_value_with_unit_masks_on_off():
    _, trace, _ = run("set the kitchen light to 50%", intent_rule("HassLightSet"))
    assert {"turn_on", "turn_off"} <= masked(trace)


def test_question_about_a_device_is_a_state_query_without_the_model():
    # Laya maps "is the X on?" to turn_on; a question naming a device can only be a query.
    response, trace, provider = run("is the front door locked?", intent_rule("HassTurnOn"))
    assert acts(response) == [("HassGetState", {"name": "lock.front_door"})]
    assert trace["segments"][0]["intent_shortcut"] == "question about a device"
    assert provider.calls == []


def test_temperature_question():
    response, *_ = run("Wie warm ist es im Bad?", intent_rule("HassTurnOn"), "de")
    assert acts(response) == [("HassClimateGetTemperature", {"area": "bathroom"})]


def test_question_without_device_goes_to_model_with_actions_masked():
    response, trace, provider = run("what is the capital of france?", intent_rule("HassTurnOn"))
    assert {"turn_on", "turn_off"} <= masked(trace)
    assert response.status == "escalate"


def test_dim_without_value_escalates_instead_of_guessing():
    response, _, provider = run("dimme das Licht", intent_rule("HassLightSet"), "de")
    assert response.reason == "missing_value"
    assert provider.calls == []


def test_low_confidence_escalates():
    response, trace, _ = run("turn on the kitchen light", intent_rule("HassTurnOn"), p=0.2)
    assert response.status == "escalate"
    assert response.reason == "low_confidence"


def test_none_escalates():
    response, *_ = run("what is the capital of france", intent_rule("nothing"))
    assert response.status == "escalate"
    assert response.reason == "none_chosen"


def test_unregistered_intents_are_not_offered():
    _, _, provider = run(
        "turn on the kitchen light", intent_rule("HassTurnOn"), intents=["HassTurnOn"]
    )
    assert set(provider.calls[0][1].options) == {"turn_on", "none"}


def test_compound_command_two_segments():
    response, trace, _ = run(
        "turn off the kitchen light and set the bathroom to 21.5 degrees",
        lambda key, q, s: (
            "HassTurnOff" if "kitchen" in s["utterance"] else "HassClimateSetTemperature"
        ),
    )
    assert acts(response) == [
        ("HassTurnOff", {"name": "light.kitchen_ceiling"}),
        ("HassClimateSetTemperature", {"area": "bathroom", "temperature": 21.5}),
    ]
    assert len(trace["segments"]) == 2


def test_conjunction_without_verb_is_one_command_with_two_targets():
    response, trace, _ = run(
        "Schalte das Licht in der Küche und im Flur aus", intent_rule("HassTurnOff"), "de"
    )
    assert len(trace["segments"]) == 1
    assert acts(response) == [
        ("HassTurnOff", {"area": "kitchen", "domain": ["light"]}),
        ("HassTurnOff", {"area": "hallway", "domain": ["light"]}),
    ]


def test_german_compound_word_mentions_area():
    response, *_ = run("mach das Wohnzimmerlicht an", intent_rule("HassTurnOn"), "de")
    assert acts(response) == [("HassTurnOn", {"area": "living_room", "domain": ["light"]})]


def test_partial_success_reports_unresolved_segment():
    response, *_ = run(
        "turn on the kitchen light and then tell me a joke",
        lambda key, q, s: "HassTurnOn" if "kitchen" in s["utterance"] else "none",
    )
    assert response.status == "ok"
    assert acts(response) == [("HassTurnOn", {"name": "light.kitchen_ceiling"})]
    assert response.unresolved == ["tell me a joke"]


def test_satellite_area_used_when_no_room_named():
    response, *_ = run("turn on the lights", intent_rule("HassTurnOn"), satellite_area_id="kitchen")
    assert acts(response) == [("HassTurnOn", {"area": "kitchen", "domain": ["light"]})]


def test_single_matching_device_is_used():
    response, *_ = run("turn on the tv", intent_rule("HassTurnOn"))
    assert acts(response) == [("HassTurnOn", {"name": "media_player.tv"})]


def test_model_target_question_for_fuzzy_names():
    response, _, provider = run(
        "turn on the desk lamps", intent_rule("HassTurnOn", target="Desk Lamp")
    )
    assert acts(response) == [("HassTurnOn", {"name": "light.desk_lamp"})]
    target_q = provider.calls[1][1]
    assert len(target_q.options) <= MAX_OPTIONS
    assert "none" in target_q.options


def test_sensitive_devices_are_never_offered_to_the_model():
    rule = intent_rule("HassTurnOn", target="Garage")
    response, _, provider = run("open the garag", rule)
    for key, q, _ in provider.calls:
        if key == "target":
            assert not any("Garage" in v for v in q.options.values())
    assert response.status == "escalate"


def test_sensitive_device_by_exact_name_is_allowed():
    response, *_ = run("lock the front door", intent_rule("HassTurnOn"))
    assert acts(response) == [("HassTurnOn", {"name": "lock.front_door"})]


def test_locks_never_targeted_by_area():
    home = Home(
        areas=HOME.areas,
        entities=[
            Entity(id="lock.back", name="Back", area_id="kitchen"),
            Entity(id="light.x", name="X", area_id="kitchen"),
        ],
    )
    response, *_ = run("lock the kitchen locks", intent_rule("HassTurnOn"), home=home)
    assert response.status == "escalate"


def test_target_question_lists_at_most_max_options():
    home = Home(
        areas=HOME.areas,
        entities=[
            Entity(id=f"light.lamp_{i}", name=f"Lamp {i} Reading", area_id="kitchen")
            for i in range(20)
        ],
    )
    _, _, provider = run(
        "turn on the reading", intent_rule("HassTurnOn", target="Lamp 1 "), home=home
    )
    target_qs = [q for key, q, _ in provider.calls if key == "target"]
    assert target_qs and len(target_qs[0].options) == MAX_OPTIONS


def test_duplicate_names_resolved_by_spoken_area():
    home = Home(
        areas=HOME.areas,
        entities=[
            Entity(id="light.a", name="Ceiling Light", area_id="kitchen"),
            Entity(id="light.b", name="Ceiling Light", area_id="bedroom"),
        ],
    )
    response, *_ = run(
        "turn on the ceiling light in the bedroom", intent_rule("HassTurnOn"), home=home
    )
    assert acts(response) == [("HassTurnOn", {"name": "light.b"})]


def test_unsupported_language_escalates():
    provider = FakeProvider(intent_rule("HassTurnOn"))
    provider.languages = ("en",)
    response, _ = decide(make_request("Licht an", "de"), provider)
    assert response.reason == "unsupported_language"


def test_segment_text_keeps_original_wording():
    response, *_ = run("Schalte das Küchenlicht aus", intent_rule("HassTurnOff"), "de")
    assert response.actions[0].segment == "Schalte das Küchenlicht aus"
    assert response.actions[0].slots == {"name": "light.kitchen_ceiling"}


def test_trace_is_complete():
    response, trace, _ = run("set the kitchen light to 50%", intent_rule("HassLightSet"))
    assert trace["trace_id"] == response.trace_id
    q = trace["segments"][0]["questions"][0]
    assert {"key", "options", "probs", "choice", "confidence", "threshold", "ms"} <= set(q)
    assert trace["segments"][0]["numbers"] == [{"value": 50.0, "unit": "pct"}]


def test_all_intents_known():
    from assist_decider_server.intents import INTENTS
    from assist_decider_server.lang import LANGS

    assert set(INTENTS) == set(ALL_INTENTS)
    for lang in LANGS.values():
        assert set(lang.intents) == set(INTENTS)


def test_fuzzy_matching_is_bounded():
    import time

    words = [f"w{i}x" for i in range(400)]
    home = Home(
        areas=HOME.areas,
        entities=[
            Entity(
                id=f"light.l{i}",
                name=" ".join(words[i : i + 3]),
                aliases=[" ".join(words[i + j : i + j + 3]) for j in range(1, 11)],
            )
            for i in range(300)
        ],
    )
    started = time.perf_counter()
    run("turn on " + " ".join(f"q{i}z" for i in range(90)), intent_rule("HassTurnOn"), home=home)
    assert time.perf_counter() - started < 5


def test_nfkc_expansion_does_not_break_the_response():
    response, *_ = run("turn on the kitchen light " + "\ufb03" * 400, intent_rule("HassTurnOn"))
    assert all(len(s) <= 500 for s in response.unresolved)


# ---------------------------------------------------------------- not expressible


def by_words(**intents: str):
    """Answer the intent question by the first word found in the segment."""

    def rule(key, q, state):
        if key != "intent":
            return "none"
        text = state["utterance"].lower()
        return next((i for w, i in intents.items() if w in text), "none")

    return rule


@pytest.mark.parametrize(
    ("text", "language", "reason"),
    [
        ("if it is cold outside set the thermostat to 24", "en", "conditional"),
        ("turn on the kitchen light unless it is warm", "en", "conditional"),
        ("Wenn es draußen kalt ist, stell die Heizung im Bad auf 22 Grad", "de", "conditional"),
        ("don't turn on the kitchen light", "en", "negation"),
        ("never open the garage door", "en", "negation"),
        ("Mach das Küchenlicht nicht an", "de", "negation"),
        ("turn on all lights except the kitchen light", "en", "exception"),
        ("turn on the kitchen light but not the desk lamp", "en", "exception"),
        ("turn on the kitchen light or the desk lamp", "en", "exception"),
        ("Schalte alle Lichter an außer das Küchenlicht", "de", "exception"),
        ("turn on the kitchen light tomorrow", "en", "scheduled"),
        ("Schalte das Küchenlicht um 7 Uhr an", "de", "scheduled"),
    ],
)
def test_sentences_that_no_intent_call_can_express_are_not_attempted(text, language, reason):
    # The exact names in these sentences would otherwise run as a plain command.
    response, trace, provider = run(text, intent_rule("HassTurnOn"), language)
    assert response.status == "escalate" and not response.actions
    assert response.reason == trace["blocked"]["reason"] == reason
    assert provider.calls == []


def test_a_blocking_word_inside_a_device_name_is_just_a_name():
    home = Home(areas=HOME.areas, entities=[Entity(id="scene.if_only", name="If Only")])
    response, *_ = run("turn on if only", intent_rule("HassTurnOn"), home=home)
    assert acts(response) == [("HassTurnOn", {"name": "scene.if_only"})]


@pytest.mark.parametrize(
    "text",
    [
        "turn on the kitchen light in 10 minutes",
        "turn on the kitchen light for 5 minutes",
        "turn on the kitchen light at 7",
        "set the kitchen light to 50 percent in 10 minutes",
    ],
)
def test_a_number_the_intent_has_no_slot_for_escalates(text):
    rule = by_words(set="HassLightSet", turn="HassTurnOn")
    response, trace, _ = run(text, rule)
    assert response.status == "escalate" and response.reason == "unused_number"
    assert "no use for" in trace["segments"][0]["note"]


def test_one_as_a_pronoun_is_not_a_stray_number():
    ctx = {"context_id": "sat_one"}
    run("turn on the kitchen light", intent_rule("HassTurnOn"), **ctx)
    response, *_ = run("turn that one off", intent_rule("HassTurnOff"), **ctx)
    assert acts(response) == [("HassTurnOff", {"name": "light.kitchen_ceiling"})]


# ---------------------------------------------------------------- on/off words


class Skewed(FakeProvider):
    """A model that is sure of one intent, whatever is asked."""

    def __init__(self, choice: str) -> None:
        super().__init__()
        self.choice = choice

    def predict(self, state, questions, lang):
        out = {}
        for key, q in questions.items():
            self.calls.append((key, q, state))
            probs = {k: 0.03 / (len(q.options) - 1) for k in q.options} | {self.choice: 0.97}
            out[key] = Answer(self.choice, probs)
        return out


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("kill the light on the desk lamp", "en"),
        ("darken the kitchen light on the left", "en"),
        ("Lösche das Licht auf dem Flur", "de"),
        ("Schalte das Licht an der Stehlampe aus", "de"),
    ],
)
def test_on_as_a_preposition_does_not_overrule_the_model(text, language):
    # "on the desk" used to mask turn_off; renormalizing the rest then ran turn_on.
    response, trace, _ = decide_with(Skewed("turn_off"), text, language)
    assert "turn_off" not in masked(trace)
    assert [a.intent for a in response.actions] == ["HassTurnOff"]


@pytest.mark.parametrize(
    ("text", "language", "hidden"),
    [
        ("turn on the kitchen light", "en", "turn_off"),
        ("turn the kitchen light on", "en", "turn_off"),
        ("turn the lights on in the kitchen", "en", "turn_off"),
        ("turn the kitchen light off please", "en", "turn_on"),
        ("Mach das Küchenlicht an", "de", "turn_off"),
        ("Mach das Licht an im Flur", "de", "turn_off"),
        ("Schalte das Licht in der Küche und im Flur aus", "de", "turn_on"),
    ],
)
def test_on_off_after_the_verb_or_at_the_end_still_guards(text, language, hidden):
    _, trace, _ = run(text, intent_rule("none"), language)
    assert hidden in masked(trace)


def test_value_preposition_is_not_an_on_word():
    # German "auf" is "on"/"open", and the preposition in "auf 50 Prozent".
    _, trace, _ = run("Stell das Küchenlicht auf 50", intent_rule("HassLightSet"), "de")
    assert "turn_off" not in masked(trace)


@pytest.mark.parametrize(
    ("text", "language", "intent"),
    [
        ("can you turn on the kitchen light?", "en", "HassTurnOn"),
        ("could you switch the kitchen light off?", "en", "HassTurnOff"),
        ("Kannst du das Küchenlicht anmachen?", "de", "HassTurnOn"),
    ],
)
def test_polite_request_with_a_question_mark_is_a_command(text, language, intent):
    response, trace, _ = run(text, intent_rule(intent), language)
    assert acts(response) == [(intent, {"name": "light.kitchen_ceiling"})]
    assert "intent_shortcut" not in trace["segments"][0]


@pytest.mark.parametrize(
    ("text", "language"), [("kitchen light on?", "en"), ("Küchenlicht an?", "de")]
)
def test_question_mark_without_a_command_word_stays_a_question(text, language):
    response, *_ = run(text, intent_rule("HassTurnOn"), language)
    assert acts(response) == [("HassGetState", {"name": "light.kitchen_ceiling"})]


def test_state_words_with_a_question_mark_never_command():
    # "open" is a verb, an on word and a state. Asked like this it must not open anything.
    response, _, provider = run("garage door open?", intent_rule("HassTurnOn"))
    assert acts(response) == [("HassGetState", {"name": "cover.garage_door"})]
    assert provider.calls == []


def test_a_device_word_that_is_also_an_on_word_is_not_a_command():
    # "lock" names the device here; the only on/off word is "off".
    _, trace, _ = run("turn off the front door lock", intent_rule("HassTurnOff"))
    assert "turn_on" in masked(trace) and "turn_off" not in masked(trace)


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("kitchen light on", "en", ("HassTurnOn", {"name": "light.kitchen_ceiling"})),
        ("Küchenlicht aus bitte", "de", ("HassTurnOff", {"name": "light.kitchen_ceiling"})),
        (
            "lights on in the hallway",
            "en",
            ("HassTurnOn", {"area": "hallway", "domain": ["light"]}),
        ),
    ],
)
def test_verbless_command_is_decided_by_its_on_off_word(text, language, expected):
    response, trace, provider = run(text, intent_rule("none"), language)
    assert acts(response) == [expected]
    assert trace["segments"][0]["intent_shortcut"] == "spoken on/off word"
    assert provider.calls == []


def test_verbless_chatter_with_an_on_word_goes_to_the_model():
    response, _, provider = run("the kitchen light was left on", intent_rule("none"))
    assert response.status == "escalate"
    assert [c[0] for c in provider.calls] == ["intent"]


# ---------------------------------------------------------------- splitting


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        (
            "kitchen light on and desk lamp off",
            "en",
            [("HassTurnOn", "light.kitchen_ceiling"), ("HassTurnOff", "light.desk_lamp")],
        ),
        (
            "turn the kitchen light on and the desk lamp off",
            "en",
            [("HassTurnOn", "light.kitchen_ceiling"), ("HassTurnOff", "light.desk_lamp")],
        ),
        (
            "Mach das Küchenlicht an und die Schreibtischlampe aus",
            "de",
            [("HassTurnOn", "light.kitchen_ceiling"), ("HassTurnOff", "light.desk_lamp")],
        ),
        (
            "Küchenlicht aus, Fernseher an",
            "de",
            [("HassTurnOff", "light.kitchen_ceiling"), ("HassTurnOn", "media_player.tv")],
        ),
    ],
)
def test_opposite_on_off_words_make_two_commands(text, language, expected):
    response, *_ = run(text, by_words(turn="HassTurnOn", mach="HassTurnOn"), language)
    assert [(a.intent, a.slots["name"]) for a in response.actions] == expected


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        (
            "set the kitchen light to 50 percent and the desk lamp to 20 percent",
            "en",
            [
                ("HassLightSet", {"name": "light.kitchen_ceiling", "brightness": 50}),
                ("HassLightSet", {"name": "light.desk_lamp", "brightness": 20}),
            ],
        ),
        (
            "Stell das Küchenlicht auf 50 Prozent und die Stehlampe auf 20 Prozent",
            "de",
            [
                ("HassLightSet", {"name": "light.kitchen_ceiling", "brightness": 50}),
                ("HassLightSet", {"name": "light.living_room_floor", "brightness": 20}),
            ],
        ),
    ],
)
def test_each_target_keeps_its_own_value(text, language, expected):
    response, *_ = run(text, intent_rule("HassLightSet"), language)
    assert acts(response) == expected


def test_a_shared_value_is_still_one_command():
    response, trace, _ = run(
        "set the kitchen light and the desk lamp to 50 percent", intent_rule("HassLightSet")
    )
    assert len(trace["segments"]) == 1
    assert [a.slots["brightness"] for a in response.actions] == [50, 50]


def test_it_refers_to_the_command_before_in_the_same_sentence():
    response, trace, _ = run(
        "dim the kitchen light to 30 percent and then switch it off",
        by_words(dim="HassLightSet", switch="HassTurnOff"),
    )
    # "switch" is the verb here, not a device word: the coffee maker stays on.
    assert acts(response) == [
        ("HassLightSet", {"name": "light.kitchen_ceiling", "brightness": 30}),
        ("HassTurnOff", {"name": "light.kitchen_ceiling"}),
    ]
    assert trace["segments"][1]["shortcut"] == "previous command"


def test_switch_as_a_verb_does_not_pick_the_only_switch():
    response, *_ = run("switch it off", intent_rule("HassTurnOff"))
    assert response.status == "escalate" and response.reason == "no_target"


def test_switch_as_a_device_word_does_not_start_a_new_command():
    response, trace, _ = run(
        "turn on the desk lamp and the kitchen switch", intent_rule("HassTurnOn")
    )
    assert len(trace["segments"]) == 1
    assert acts(response) == [
        ("HassTurnOn", {"name": "light.desk_lamp"}),
        ("HassTurnOn", {"area": "kitchen", "domain": ["switch"]}),
    ]


# ---------------------------------------------------------------- targets


def test_device_words_inside_names_do_not_spread_to_rooms():
    response, *_ = run(
        "turn on the lights in the kitchen and the living room and the tv",
        intent_rule("HassTurnOn"),
    )
    assert acts(response) == [
        ("HassTurnOn", {"name": "media_player.tv"}),
        ("HassTurnOn", {"area": "kitchen", "domain": ["light"]}),
        ("HassTurnOn", {"area": "living_room", "domain": ["light"]}),
    ]


def test_room_is_a_target_unless_it_only_locates_the_named_device():
    response, *_ = run(
        "turn on the floor lamp and the lights in the living room", intent_rule("HassTurnOn")
    )
    assert acts(response) == [
        ("HassTurnOn", {"name": "light.living_room_floor"}),
        ("HassTurnOn", {"area": "living_room", "domain": ["light"]}),
    ]
    response, *_ = run("turn on the floor lamp in the living room", intent_rule("HassTurnOn"))
    assert acts(response) == [("HassTurnOn", {"name": "light.living_room_floor"})]


def test_room_takes_the_kind_of_the_named_device():
    response, *_ = run("turn on the kitchen light and the bedroom", intent_rule("HassTurnOn"))
    assert acts(response)[1] == ("HassTurnOn", {"area": "bedroom", "domain": ["light"]})


def test_named_device_limits_the_intents():
    _, trace, _ = run("set the kitchen light to 20", intent_rule("HassLightSet"))
    assert {"set_temperature", "set_position"} <= masked(trace)
    reasons = trace["segments"][0]["dropped_intents"]
    assert reasons["HassClimateSetTemperature"] == "does not fit the named device"


def test_device_named_like_an_everyday_word_does_not_capture_the_word():
    home = Home(
        areas=HOME.areas,
        entities=[
            *HOME.entities,
            Entity(id="sensor.temperature", name="Temperature", area_id="kitchen"),
        ],
    )
    response, *_ = run(
        "set the temperature to 22 degrees",
        intent_rule("HassClimateSetTemperature"),
        home=home,
        satellite_area_id="bathroom",
    )
    assert acts(response) == [
        ("HassClimateSetTemperature", {"area": "bathroom", "temperature": 22.0})
    ]


def test_named_device_is_never_dropped_to_make_a_follow_up_fit():
    # Scenes cannot be turned off. Dropping the name would turn the kitchen light off again.
    home = Home(
        areas=HOME.areas, entities=[*HOME.entities, Entity(id="scene.movie", name="Movie Night")]
    )
    ctx = {"context_id": "sat_incompatible", "home": home}
    run("turn off the kitchen light", intent_rule("HassTurnOff"), **ctx)
    response, _, provider = run("and movie night too", **ctx)
    assert response.status == "escalate" and response.reason == "incompatible_target"
    assert provider.calls == []


# ---------------------------------------------------------------- follow-ups


def test_follow_up_reuses_targets_then_intent():
    ctx = {"context_id": "sat_follow_up"}
    run("turn on the kitchen light", intent_rule("HassTurnOn"), **ctx)
    response, trace, _ = run("turn it off", intent_rule("HassTurnOff"), **ctx)
    assert acts(response) == [("HassTurnOff", {"name": "light.kitchen_ceiling"})]
    assert trace["segments"][0]["shortcut"] == "previous command"

    response, trace, provider = run("and the hallway too", **ctx)
    assert acts(response) == [("HassTurnOff", {"area": "hallway", "domain": ["light"]})]
    assert trace["segments"][0]["intent_shortcut"] == "previous command"
    assert not provider.calls


def test_verbless_chatter_does_not_repeat_the_previous_command():
    ctx = {"context_id": "sat_thanks"}
    run("turn on the kitchen light", intent_rule("HassTurnOn"), **ctx)
    response, _, provider = run("thanks", intent_rule("none"), **ctx)
    assert response.status == "escalate"
    assert [c[0] for c in provider.calls] == ["intent"]  # the model was asked, not skipped


def test_follow_up_with_a_new_value():
    ctx = {"context_id": "sat_value"}
    run("set the bathroom heating to 21 degrees", intent_rule("HassClimateSetTemperature"), **ctx)
    response, *_ = run("23 degrees", **ctx)
    assert acts(response) == [
        ("HassClimateSetTemperature", {"name": "climate.bathroom", "temperature": 23.0})
    ]


def test_memory_expires_and_is_per_context(monkeypatch):
    from assist_decider_server import pipeline

    now = [1000.0]
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])
    run("turn on the kitchen light", intent_rule("HassTurnOn"), context_id="sat_a")
    response, *_ = run("turn it off", intent_rule("HassTurnOff"), context_id="sat_b")
    assert response.status == "escalate"
    now[0] += 61
    response, *_ = run("turn it off", intent_rule("HassTurnOff"), context_id="sat_a")
    assert response.status == "escalate"


def test_follow_up_needs_every_word_accounted_for():
    for n, text in enumerate(("I am going to the bedroom now", "the bedroom is a mess")):
        ctx = {"context_id": f"sat_chatter_{n}"}
        run("turn off the kitchen light", intent_rule("HassTurnOff"), **ctx)
        response, trace, provider = run(text, intent_rule("none"), **ctx)
        assert response.status == "escalate", text
        assert "intent_shortcut" not in trace["segments"][0]
        assert [c[0] for c in provider.calls] == ["intent"]  # the model decides, not memory


def test_previous_targets_need_a_bare_reference():
    ctx = {"context_id": "sat_everything"}
    run("turn on the tv", intent_rule("HassTurnOn"), **ctx)
    response, *_ = run("turn off everything", intent_rule("HassTurnOff"), **ctx)
    assert response.status == "escalate"


def test_locks_are_not_reached_through_it():
    ctx = {"context_id": "sat_lock"}
    response, *_ = run("lock the front door", intent_rule("HassTurnOn"), **ctx)
    assert acts(response) == [("HassTurnOn", {"name": "lock.front_door"})]
    response, *_ = run("turn it off", intent_rule("HassTurnOff"), **ctx)
    assert response.status == "escalate" and response.reason == "no_target"
    response, *_ = run(
        "open the garage door and then close it",
        by_words(open="HassTurnOn", close="HassTurnOff"),
    )
    assert acts(response) == [("HassTurnOn", {"name": "cover.garage_door"})]
    assert response.unresolved == ["close it"]


def test_bare_on_off_word_follows_up_on_the_previous_devices():
    ctx = {"context_id": "sat_aus"}
    run("Mach das Küchenlicht an", intent_rule("HassTurnOn"), "de", **ctx)
    response, *_ = run("aus", intent_rule("none"), "de", **ctx)
    assert acts(response) == [("HassTurnOff", {"name": "light.kitchen_ceiling"})]
