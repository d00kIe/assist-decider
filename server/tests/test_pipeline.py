from __future__ import annotations

from assist_decider_server.intents import ACTIONS, DOMAIN_ACTIONS
from assist_decider_server.lang import LANGS
from assist_decider_server.pipeline import MAX_OPTIONS, decide
from assist_decider_server.protocol import Entity, Home

from .conftest import HOME, FakeProvider, answer, make_request

KITCHEN_LIGHT = {"name": "light.kitchen_ceiling"}


def run(text: str, rule=None, language: str = "en", p: float = 0.95, **kw):
    provider = FakeProvider(rule, p=p)
    response, trace = decide(make_request(text, language, **kw), provider)
    return response, trace, provider


def acts(response):
    return [(a.intent, a.slots) for a in response.actions]


def masked(trace, n):
    return set(trace["questions"][n]["masked"])


# ---------------------------------------------------------------- devices


def test_named_device_needs_no_which_question():
    response, trace, provider = run("turn on the kitchen light")
    assert acts(response) == [("HassTurnOn", KITCHEN_LIGHT)]
    assert provider.asked() == ["kind", "action"]
    assert trace["said"] == ["light.kitchen_ceiling"]


def test_action_question_names_the_device_as_written_in_home_assistant():
    _, _, provider = run("schalte das küchenlicht ein", language="de")
    assert provider.calls[1][1].instructions == "Was will der Nutzer mit Küchenlicht?"


def test_brightness_value_comes_from_the_value_question():
    response, _, provider = run("set the kitchen light to 50%", answer(action="set_brightness"))
    assert acts(response) == [("HassLightSet", KITCHEN_LIGHT | {"brightness": 50})]
    assert provider.asked() == ["kind", "action", "value"]
    assert set(provider.calls[2][1].options) == {"50", "none"}


def test_room_asks_which_of_its_devices():
    response, _, provider = run("turn on the light in the kitchen", answer(device="Kitchen Light"))
    assert acts(response) == [("HassTurnOn", KITCHEN_LIGHT)]
    which = provider.calls[1][1]
    assert set(which.options) == {"light.kitchen_ceiling", "switch.coffee_maker"}


def test_room_is_skipped_when_one_of_its_devices_is_named():
    response, _, provider = run("turn on the coffee maker in the kitchen")
    assert acts(response) == [("HassTurnOn", {"name": "switch.coffee_maker"})]
    assert "which" not in provider.asked()


def test_fit_offers_only_devices_that_take_the_number():
    # Living room: floor lamp, thermostat, blinds, TV. Only the thermostat takes "22 degrees".
    response, _, provider = run(
        "set the living room to 22 degrees", answer(action="set_temperature")
    )
    assert acts(response) == [
        ("HassClimateSetTemperature", {"name": "climate.living_room", "temperature": 22.0})
    ]
    assert "which" not in provider.asked()


def test_fit_keeps_every_device_when_none_takes_the_number():
    _, _, provider = run("turn on the living room at 7 hours", answer())
    which = next(q for kind, q, _ in provider.calls if kind == "which")
    assert len(which.options) == 4


def test_german_compound_names_a_room():
    response, *_ = run("mach das Wohnzimmerlicht an", answer(device="Floor Lamp"), "de")
    assert acts(response) == [("HassTurnOn", {"name": "light.living_room_floor"})]


def test_speakers_room_when_nothing_is_named():
    response, *_ = run("turn on the light", answer(device="Kitchen"), satellite_area_id="kitchen")
    assert acts(response) == [("HassTurnOn", KITCHEN_LIGHT)]


def test_nothing_named_and_no_room_is_handed_off_without_the_model():
    response, _, provider = run("turn on the light")
    assert response.status == "escalate" and response.reason == "no_target"
    assert response.unresolved == ["turn on the light"]
    assert provider.calls == []


def test_locks_and_garage_doors_are_never_picked_from_a_room():
    home = Home(
        areas=HOME.areas,
        entities=[
            Entity(id="lock.back", name="Back Door", area_id="kitchen"),
            Entity(id="cover.garage", name="Garage", area_id="kitchen", device_class="garage"),
            Entity(id="light.x", name="X", area_id="kitchen"),
        ],
    )
    response, _, provider = run("lock the kitchen", answer(action="turn_on"), home=home)
    assert acts(response) == [("HassTurnOn", {"name": "light.x"})]
    assert "which" not in provider.asked()


def test_lock_by_its_exact_name():
    response, *_ = run("lock the front door", answer(action="lock"))
    assert acts(response) == [("HassTurnOn", {"name": "lock.front_door"})]


def test_same_name_in_two_rooms_is_narrowed_by_the_room_said():
    home = Home(
        areas=HOME.areas,
        entities=[
            Entity(id="light.a", name="Ceiling Light", area_id="kitchen"),
            Entity(id="light.b", name="Ceiling Light", area_id="bedroom"),
        ],
    )
    response, *_ = run("turn on the ceiling light in the bedroom", home=home)
    assert acts(response) == [("HassTurnOn", {"name": "light.b"})]


def test_device_of_an_unsupported_kind_is_not_used():
    home = Home(areas=HOME.areas, entities=[Entity(id="vacuum.robbie", name="Robbie")])
    response, *_ = run("start robbie", home=home)
    assert response.reason == "no_target"


def test_a_device_said_twice_acts_once():
    response, *_ = run("turn on the kitchen light and the kitchen light")
    assert acts(response) == [("HassTurnOn", KITCHEN_LIGHT)]


def test_big_room_is_narrowed_by_the_kind_of_device_said():
    home = Home(
        areas=HOME.areas,
        entities=[
            *(Entity(id=f"switch.s{i}", name=f"Plug {i}", area_id="kitchen") for i in range(12)),
            Entity(id="light.k", name="Ceiling", area_id="kitchen"),
        ],
    )
    response, *_ = run("turn on the light in the kitchen", home=home)
    assert acts(response) == [("HassTurnOn", {"name": "light.k"})]
    response, *_ = run("turn on the kitchen", home=home)
    assert response.reason == "too_many_devices"


def test_which_question_stays_small():
    home = Home(
        areas=HOME.areas,
        entities=[Entity(id=f"light.l{i}", name=f"Lamp {i}", area_id="kitchen") for i in range(9)],
    )
    _, _, provider = run("turn on the kitchen", home=home)
    which = next(q for kind, q, _ in provider.calls if kind == "which")
    assert len(which.options) <= MAX_OPTIONS


# ---------------------------------------------------------------- actions


def test_question_allows_only_query():
    response, trace, _ = run("is the front door locked", answer(kind="question", action="lock"))
    # The model's "lock" is masked: a question never changes anything.
    assert acts(response) == [("HassGetState", {"name": "lock.front_door"})]
    assert {"lock", "unlock"} <= masked(trace, 1)


def test_temperature_question_about_a_thermostat():
    response, *_ = run("wie warm ist es im Bad", answer(kind="question", action="query"), "de")
    assert acts(response) == [("HassClimateGetTemperature", {"name": "climate.bathroom"})]


def test_command_never_answers_with_query():
    _, trace, _ = run("turn on the kitchen light")
    assert "query" in masked(trace, 1)


def test_off_word_masks_turn_on():
    # The model prefers "turn on"; the spoken "aus" rules it out.
    response, trace, _ = run(
        "mach das Küchenlicht aus",
        answer(action="turn_on"),
        "de",
        options={"confidence_threshold": 0},
    )
    assert "turn_on" in masked(trace, 1)
    assert acts(response) and acts(response)[0][0] != "HassTurnOn"


def test_near_each_device_follows_the_nearer_on_off_word():
    _, trace, _ = run(
        "turn on the kitchen light and turn off the hallway light",
        answer(action={"Kitchen Light": "turn_on", "Hallway Light": "turn_off"}),
    )
    assert "turn_off" in masked(trace, 1) and "turn_on" not in masked(trace, 1)
    assert "turn_on" in masked(trace, 2) and "turn_off" not in masked(trace, 2)


def test_lock_words_replace_on_off_words_for_locks():
    # "close"/"ab" mean off for a light but lock for a lock; "open"/"auf" unlock it.
    for text, lang, want, ruled_out in [
        ("sperr die Haustür ab", "de", "lock", "unlock"),
        ("schließ die Haustür ab", "de", "lock", "unlock"),
        ("sperr die Haustür auf", "de", "unlock", "lock"),
        ("öffne die Haustür", "de", "unlock", "lock"),
        ("open the front door", "en", "unlock", "lock"),
        ("lock the front door", "en", "lock", "unlock"),
    ]:
        response, trace, _ = run(text, answer(action=want), language=lang)
        intent = "HassTurnOn" if want == "lock" else "HassTurnOff"
        assert acts(response) == [(intent, {"name": "lock.front_door"})], text
        assert ruled_out in masked(trace, 1), text


def test_actions_without_a_registered_intent_are_masked():
    _, trace, _ = run("turn on the kitchen light", intents=["HassTurnOn"])
    assert {"turn_off", "set_brightness"} <= masked(trace, 1)


def test_no_number_said_hands_off_without_asking_for_a_value():
    response, _, provider = run("dim the kitchen light", answer(action="set_brightness"))
    assert response.reason == "missing_value"
    assert "value" not in provider.asked()


def test_no_value_chosen_hands_off():
    response, *_ = run("set the kitchen light to 50", answer(action="set_brightness", value="none"))
    assert response.reason == "missing_value"


def test_value_out_of_range_hands_off():
    response, *_ = run("set the bathroom heating to 70", answer(action="set_temperature"))
    assert response.reason == "value_not_possible"


def test_temperature_keeps_half_degrees():
    response, *_ = run(
        "stell die Heizung Bad auf einundzwanzig komma fünf Grad",
        answer(action="set_temperature"),
        "de",
    )
    assert acts(response) == [
        ("HassClimateSetTemperature", {"name": "climate.bathroom", "temperature": 21.5})
    ]


# ---------------------------------------------------------------- hand-offs


def test_low_confidence_hands_off_the_whole_sentence():
    response, *_ = run("turn on the kitchen light and the tv", p=0.55)
    assert response.status == "escalate" and not response.actions
    assert response.reason == "low_confidence"


def test_conditional_sentence_is_handed_off_without_the_model():
    response, _, provider = run("turn on the kitchen light if it is cold outside")
    assert response.reason == "conditional"
    assert provider.calls == []


def test_unsupported_language():
    provider = FakeProvider()
    provider.languages = ("en",)
    response, _ = decide(make_request("Licht an", "de"), provider)
    assert response.reason == "unsupported_language"


# ---------------------------------------------------------------- follow-ups


def test_follow_up_uses_the_previous_devices():
    ctx = {"context_id": "sat_follow_up"}
    run("turn on the kitchen light", **ctx)
    response, trace, _ = run("turn it off", answer(action="turn_off"), **ctx)
    assert acts(response) == [("HassTurnOff", KITCHEN_LIGHT)]
    assert any(s["check"] == "previous command" and s["ok"] for s in trace["steps"])


def test_a_kind_of_device_said_is_not_a_follow_up():
    ctx = {"context_id": "sat_kind", "satellite_area_id": "bedroom"}
    run("turn on the kitchen light", **ctx)
    response, *_ = run("turn off the light", answer(action="turn_off"), **ctx)
    assert acts(response) == [("HassTurnOff", {"name": "light.desk_lamp"})]


def test_memory_expires_and_is_per_context(monkeypatch):
    from assist_decider_server import pipeline

    now = [1000.0]
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])
    run("turn on the kitchen light", context_id="sat_a")
    response, *_ = run("turn it off", context_id="sat_b")
    assert response.status == "escalate"
    now[0] += 61
    response, *_ = run("turn it off", context_id="sat_a")
    assert response.status == "escalate"


# ---------------------------------------------------------------- trace and data


def test_trace_is_complete():
    response, trace, _ = run("set the kitchen light to 50%", answer(action="set_brightness"))
    assert trace["trace_id"] == response.trace_id
    assert trace["numbers"] == [{"value": 50.0, "unit": "pct"}]
    q = trace["questions"][0]
    assert {"options", "probs", "choice", "confidence", "threshold", "ms"} <= set(q)
    assert response.actions[0].segment == "set the kitchen light to 50%"


def test_nfkc_expansion_does_not_break_the_response():
    response, *_ = run("turn on the kitchen light " + "ﬃ" * 400, p=0.55)
    assert all(len(s) <= 500 for s in response.unresolved)


def test_every_action_is_worded_in_every_language():
    used = {a for actions in DOMAIN_ACTIONS.values() for a in actions}
    assert used == set(ACTIONS)
    for lang in LANGS.values():
        assert set(lang.actions) == set(ACTIONS)
