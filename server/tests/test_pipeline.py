from __future__ import annotations

from assist_decider_server.pipeline import MAX_OPTIONS, decide
from assist_decider_server.protocol import Entity, Home

from .conftest import ALL_INTENTS, HOME, FakeProvider, intent_rule, make_request


def run(text: str, rule=None, language: str = "en", p: float = 0.95, **kw):
    provider = FakeProvider(rule, p=p)
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
