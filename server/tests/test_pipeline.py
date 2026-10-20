from __future__ import annotations

from assist_decider_server.intents import ACTIONS, DOMAIN_ACTIONS
from assist_decider_server.lang import LANGS
from assist_decider_server.pipeline import decide
from assist_decider_server.protocol import Area, Entity, Floor, Home

from .conftest import HOME, FakeProvider, answer, make_request

KITCHEN_LIGHT = {"name": "light.kitchen_ceiling"}
HALLWAY_LIGHT = {"name": "light.hallway"}


def run(text: str, rule=None, language: str = "en", p: float = 0.95, **kw):
    provider = FakeProvider(rule, p=p)
    response, trace = decide(make_request(text, language, **kw), provider)
    return response, trace, provider


def acts(response):
    return [(a.intent, a.slots) for a in response.actions]


def masked(trace, key):
    return set(next(q for q in trace["questions"] if q["key"] == key)["masked"])


def question(provider, kind, n=0):
    """The n-th question of a kind ("action", "which"…) the provider was asked."""
    return [q for k, q, _ in provider.calls if k == kind][n]


# ---------------------------------------------------------------- devices


def test_named_device_needs_no_which_question():
    response, trace, provider = run("turn on the kitchen light")
    assert acts(response) == [("HassTurnOn", KITCHEN_LIGHT)]
    assert provider.asked() == ["action"]
    assert trace["said"] == ["light.kitchen_ceiling"]


def test_action_question_names_the_device_as_written_in_home_assistant():
    _, _, provider = run("schalte das küchenlicht ein", language="de")
    assert question(provider, "action").instructions == "Was will der Nutzer mit Küchenlicht?"


def test_the_only_number_that_fits_needs_no_value_question():
    response, _, provider = run("set the kitchen light to 50%", answer(action="set_brightness"))
    assert acts(response) == [("HassLightSet", KITCHEN_LIGHT | {"brightness": 50})]
    assert provider.asked() == ["action"]


def test_two_numbers_ask_which_one_in_the_same_call():
    response, _, provider = run(
        "set the bathroom heating to 21 or 23 degrees", answer(action="set_temperature", value="23")
    )
    assert acts(response) == [
        ("HassClimateSetTemperature", {"name": "climate.bathroom", "temperature": 23.0})
    ]
    assert provider.asked() == ["action", "value"]
    assert len(provider.batches) == 1
    assert set(question(provider, "value").options) == {"21", "23", "none"}


def test_room_asks_the_kind_and_uses_its_only_device_of_it():
    response, _, provider = run("turn on the light in the kitchen", answer(of="light"))
    assert acts(response) == [("HassTurnOn", KITCHEN_LIGHT)]
    assert set(question(provider, "device_kind").options) == {"light", "switch"}
    assert "which" not in provider.asked()


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
    assert len(question(provider, "device_kind").options) == 4


def test_german_compound_names_a_room():
    response, *_ = run("mach das Wohnzimmerlicht an", answer(of="light"), "de")
    assert acts(response) == [("HassTurnOn", {"name": "light.living_room_floor"})]


def test_speakers_room_when_nothing_is_named():
    response, *_ = run("turn on the light", answer(of="light"), satellite_area_id="kitchen")
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


def test_big_room_asks_the_kind_first():
    home = Home(
        areas=HOME.areas,
        entities=[
            *(Entity(id=f"switch.s{i}", name=f"Plug {i}", area_id="kitchen") for i in range(12)),
            Entity(id="light.k", name="Ceiling", area_id="kitchen"),
        ],
    )
    response, _, provider = run("turn on the light in the kitchen", answer(of="light"), home=home)
    assert acts(response) == [("HassTurnOn", {"name": "light.k"})]
    assert "which" not in provider.asked()
    # All twelve plugs: one area action, however many there are.
    response, *_ = run("turn on the plugs in the kitchen", answer(of="switch"), home=home)
    assert acts(response) == [("HassTurnOn", {"area": "kitchen", "domain": ["switch"]})]


def test_a_room_means_every_device_of_the_kind_without_asking_which():
    home = Home(
        areas=HOME.areas,
        entities=[Entity(id=f"light.l{i}", name=f"Lamp {i}", area_id="kitchen") for i in range(3)],
    )
    response, _, provider = run("set the light to 100%", home=home, satellite_area_id="kitchen")
    assert acts(response) == [
        ("HassLightSet", {"area": "kitchen", "domain": ["light"], "brightness": 100})
    ]
    assert "which" not in provider.asked()
    assert question(provider, "action").instructions == "What does the user want with the lights?"


def test_one_blind_in_the_room_needs_no_one_or_all_answer():
    # From the live log: "close" at 0.98 was handed off because "one or all?" was unsure.
    response, _, provider = run(
        "close the blinds in the living room", answer(action="close", of="cover")
    )
    assert acts(response) == [("HassTurnOff", {"name": "cover.living_room_blinds"})]
    assert "scope" not in provider.asked()


# ---------------------------------------------------------------- actions


def test_a_question_is_the_model_s_query_answer():
    response, trace, _ = run("is the front door locked", answer(action="query"))
    assert acts(response) == [("HassGetState", {"name": "lock.front_door"})]
    assert masked(trace, "action:lock.front_door") == set()


def test_temperature_question_about_a_thermostat():
    response, *_ = run("wie warm ist es im Bad", answer(kind="question", action="query"), "de")
    assert acts(response) == [("HassClimateGetTemperature", {"name": "climate.bathroom"})]


def test_command_or_question_weighs_the_action_but_never_vetoes_it():
    # From the live log: "Macht es Esstisch Licht an" was asked as a question (0.8) and only
    # read the light's state, though the action answer was a sure "turn on".
    def rule(kind, q, state):
        return "question" if kind == "kind" else answer()(kind, q, state)

    response, *_ = run("mach das Küchenlicht an", rule, "de")
    assert acts(response) == [("HassTurnOn", KITCHEN_LIGHT)]
    # Both say "ask": a question.
    response, *_ = run("ist das Küchenlicht an", answer(action="query"), "de")
    assert acts(response) == [("HassGetState", KITCHEN_LIGHT)]


def test_the_model_decides_on_or_off_without_word_lists():
    response, trace, _ = run("mach das Küchenlicht aus", answer(action="turn_off"), "de")
    assert acts(response) == [("HassTurnOff", KITCHEN_LIGHT)]
    assert masked(trace, "action:light.kitchen_ceiling") == {"set_brightness"}  # no number


def test_each_device_is_asked_again_with_its_own_words():
    response, trace, provider = run(
        "turn on the kitchen light and turn off the hallway light",
        answer(action={"Kitchen Light": "turn_on", "Hallway Light": "turn_off"}),
    )
    assert acts(response) == [("HassTurnOn", KITCHEN_LIGHT), ("HassTurnOff", HALLWAY_LIGHT)]
    own = [state["utterance"] for kind, _, state in provider.calls if kind == "action"][2:]
    assert own == ["turn on the kitchen light and turn", "off the hallway light"]
    assert [q["utterance"] for q in trace["questions"] if "utterance" in q] == own


def test_two_views_that_disagree_lower_the_confidence():
    def rule(kind, q, state):
        if kind == "action":  # on from the whole sentence, off from its own words
            return "turn_on" if state["utterance"].startswith("turn on the kitchen") else "turn_off"
        return answer()(kind, q, state)

    response, trace, _ = run("turn on the kitchen light and turn off the hallway light", rule)
    assert response.reason == "low_confidence"
    hallway = next(q for q in trace["questions"] if q["key"] == "action:light.hallway")
    assert hallway["confidence"] < 0.4  # on and off half each


def test_one_name_is_asked_once():
    _, _, provider = run("turn on the kitchen light")
    assert provider.asked() == ["action"]


def test_a_number_in_a_devices_own_words_is_its_value():
    response, _, provider = run(
        "set the thermostat to 22 degrees and the bathroom heating to 24",
        answer(action="set_temperature"),
    )
    assert acts(response) == [
        ("HassClimateSetTemperature", {"name": "climate.living_room", "temperature": 22.0}),
        ("HassClimateSetTemperature", {"name": "climate.bathroom", "temperature": 24.0}),
    ]
    assert "value" not in provider.asked()


def test_actions_without_a_registered_intent_are_masked():
    _, trace, _ = run("turn on the kitchen light", intents=["HassTurnOn"])
    assert {"turn_off", "set_brightness"} <= masked(trace, "action:light.kitchen_ceiling")


def test_dim_without_a_number_is_a_step():
    response, trace, provider = run("dim the kitchen light", answer(action="dimmer"))
    assert acts(response) == [("HassLightSet", KITCHEN_LIGHT | {"brightness_step": -25})]
    assert "set_brightness" in masked(trace, "action:light.kitchen_ceiling")
    assert "value" not in provider.asked()


def test_too_dark_brightens_every_light_in_the_room():
    home = Home(
        areas=HOME.areas,
        entities=[Entity(id=f"light.l{i}", name=f"Lamp {i}", area_id="kitchen") for i in range(2)],
    )
    response, *_ = run(
        "it's too dark in here", answer(action="brighter"), home=home, satellite_area_id="kitchen"
    )
    assert acts(response) == [
        ("HassLightSet", {"area": "kitchen", "domain": ["light"], "brightness_step": 25})
    ]


def test_warmer_is_a_temperature_step():
    response, *_ = run("make the bathroom heating warmer", answer(action="warmer"))
    assert acts(response) == [
        ("HassClimateSetTemperature", {"name": "climate.bathroom", "temperature_step": 1})
    ]


def test_set_without_a_number_hands_off_where_there_is_no_step():
    response, _, provider = run("open the living room blinds a bit", answer(action="set_position"))
    assert response.reason == "missing_value"
    assert "value" not in provider.asked()


def test_on_with_a_percentage_sets_the_value():
    # From the live log: "Setzt das Licht auf 100%": on 0.51, set to a value 0.49.
    def rule(kind, q, state):
        if kind == "action":
            return "turn_on"
        return answer()(kind, q, state)

    response, *_ = run("schalte das Küchenlicht auf hundert Prozent", rule, "de")
    assert acts(response) == [("HassLightSet", KITCHEN_LIGHT | {"brightness": 100})]


def test_dim_to_a_percentage_sets_the_value():
    response, *_ = run("dim the kitchen light to 30 percent", answer(action="dimmer"))
    assert acts(response) == [("HassLightSet", KITCHEN_LIGHT | {"brightness": 30})]


def test_a_lock_below_the_safety_minimum_is_handed_off_even_at_threshold_0():
    response, *_ = run(
        "unlock the front door", answer(action="unlock"), p=0.6, options={"confidence_threshold": 0}
    )
    assert response.reason == "low_confidence"


def test_a_step_with_a_bare_number_sets_it():
    response, *_ = run("dim the kitchen light to 20", answer(action="dimmer"))
    assert acts(response) == [("HassLightSet", KITCHEN_LIGHT | {"brightness": 20})]


def test_a_bare_number_does_not_turn_on_into_a_value():
    # "the right one" says 1, without a unit: "turn on" stays "turn on".
    response, *_ = run("turn on the kitchen light one", answer(action="turn_on"))
    assert acts(response) == [("HassTurnOn", KITCHEN_LIGHT)]


def test_no_value_chosen_hands_off():
    response, *_ = run(
        "set the bathroom heating to 21 or 23", answer(action="set_temperature", value="none")
    )
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


def test_request_not_about_the_home_is_handed_off():
    response, *_ = run("tell me a joke", answer(kind="other"), satellite_area_id="kitchen")
    assert response.reason == "not_for_home"


def test_topic_names_the_kinds_of_device_the_home_has():
    *_, provider = run("turn on the kitchen light")
    home = (
        "lights, switches, blinds, heating, TV, locks or other devices at home,"
        " or how warm, cold, bright or dark it is"
    )
    assert question(provider, "topic").options["home"] == home
    *_, provider = run("turn on the kitchen light", home=Home(entities=HOME.entities[:1]))
    assert question(provider, "topic").options["home"].startswith("lights or other devices")


def test_changing_a_lock_needs_more_than_the_threshold():
    low = {"p": 0.6, "options": {"confidence_threshold": 0.1}}  # command or question: 0.2
    response, *_ = run("lock the front door", answer(action="lock"), **low)
    assert response.reason == "low_confidence"
    response, *_ = run("lock the kitchen light", answer(action="turn_on"), **low)
    assert acts(response) == [("HassTurnOn", KITCHEN_LIGHT)]
    response, *_ = run("is the front door locked", answer("question", "query"), **low)
    assert acts(response) == [("HassGetState", {"name": "lock.front_door"})]


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


def test_the_model_decides_whether_it_is_a_follow_up():
    ctx = {"context_id": "sat_kind", "satellite_area_id": "bedroom"}
    run("turn on the kitchen light", **ctx)
    rule = answer(action="turn_off", reference="other")
    response, _, provider = run("turn off the light", rule, **ctx)
    assert acts(response) == [("HassTurnOff", {"name": "light.desk_lamp"})]
    assert "'turn on the kitchen light'" in question(provider, "reference").instructions


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


# ---------------------------------------------------------------- places, all, floors

FLOORS = Home(
    floors=[
        Floor(id="ground", name="Ground Floor", aliases=["Erdgeschoss"]),
        Floor(id="upstairs", name="Second Floor", aliases=["Obergeschoss"]),
    ],
    areas=[
        Area(id="living", name="Living Room", aliases=["Wohnzimmer"], floor_id="ground"),
        Area(id="garage", name="Garage", floor_id="ground"),
        Area(id="office", name="Office", aliases=["Büro"], floor_id="upstairs"),
        Area(id="kids", name="Kids Room", floor_id="upstairs"),
    ],
    entities=[
        Entity(id="light.left", name="Left Light", aliases=["Linkes Licht"], area_id="living"),
        Entity(id="light.right", name="Right Light", aliases=["Rechtes Licht"], area_id="living"),
        Entity(id="media_player.tv", name="TV", area_id="living"),
        Entity(id="climate.living", name="Thermostat", area_id="living"),
        Entity(id="cover.office", name="Office Blinds", area_id="office", device_class="blind"),
        Entity(id="cover.kids", name="Kids Blinds", area_id="kids", device_class="blind"),
        Entity(id="cover.gate", name="Gate", area_id="garage", device_class="gate"),
        Entity(id="cover.awning", name="Awning", area_id="garage", device_class="awning"),
        Entity(id="light.desk", name="Desk Light", area_id="office"),
    ],
)


def floors(text, rule=None, language="en", **kw):
    return run(text, rule, language, home=FLOORS, **kw)


def test_all_of_a_kind_in_a_room_is_one_area_action():
    response, *_ = floors("turn on all the lights in the living room", answer(of="light"))
    assert acts(response) == [("HassTurnOn", {"area": "living", "domain": ["light"]})]


def test_a_floor_is_one_action_per_room_on_it():
    response, *_ = floors(
        "close all the covers on the second floor", answer(action="close", of="cover")
    )
    assert acts(response) == [
        ("HassTurnOff", {"area": "office", "domain": ["cover"]}),
        ("HassTurnOff", {"area": "kids", "domain": ["cover"]}),
    ]


def test_all_never_takes_a_garage_door_along():
    # The gate shares the garage with the awning: the awning is named, the gate left alone.
    response, *_ = floors(
        "close all the covers on the ground floor", answer(action="close", of="cover")
    )
    assert acts(response) == [("HassTurnOff", {"name": "cover.awning"})]


def test_this_floor_is_the_speakers_floor():
    response, *_ = floors(
        "close all the blinds on this floor",
        answer(action="close", scope="all", of="cover", place="floor"),
        satellite_area_id="office",
    )
    assert acts(response) == [
        ("HassTurnOff", {"area": "office", "domain": ["cover"]}),
        ("HassTurnOff", {"area": "kids", "domain": ["cover"]}),
    ]


def test_the_follow_up_after_all_means_every_device_of_it():
    ctx = {"context_id": "sat_all"}
    floors("turn on all the lights in the living room", answer(of="light"), **ctx)
    response, *_ = floors("turn them off", answer(action="turn_off"), **ctx)
    assert acts(response) == [
        ("HassTurnOff", {"name": "light.left"}),
        ("HassTurnOff", {"name": "light.right"}),
    ]


def test_climate_for_a_whole_room_has_no_domain_slot():
    home = FLOORS.model_copy(
        update={
            "entities": [
                *FLOORS.entities,
                Entity(id="climate.living_floor", name="Radiator", area_id="living"),
            ]
        }
    )
    rule = answer(action="set_temperature", of="climate")
    response, *_ = run("set the heating in the living room to 21", rule, home=home)
    assert acts(response) == [
        ("HassClimateSetTemperature", {"area": "living", "temperature": 21.0})
    ]
    rule = answer(action="turn_on", of="climate")
    response, *_ = run("warm up the living room", rule, home=home)
    assert acts(response) == [("HassTurnOn", {"area": "living", "domain": ["climate"]})]


def test_a_question_about_all_of_them_is_handed_off():
    rule = answer(action="query", of="light")
    response, *_ = floors("are the lights in the living room on", rule)
    assert response.reason == "unsupported"


def test_a_question_about_the_sensors_of_a_room_picks_one():
    home = Home(
        areas=HOME.areas,
        entities=[
            Entity(id="sensor.temp", name="Bad Temperatur", area_id="bathroom"),
            Entity(id="sensor.hum", name="Bad Feuchtigkeit", area_id="bathroom"),
        ],
    )
    rule = answer(action="query", device="Feuchtigkeit")
    response, *_ = run(
        "wie feucht ist es hier", rule, "de", home=home, satellite_area_id="bathroom"
    )
    assert acts(response) == [("HassGetState", {"name": "sensor.hum"})]


def test_a_name_said_with_another_ending_counts():
    response, *_ = floors("mach das linke Licht aus", answer(action="turn_off"), "de")
    assert acts(response) == [("HassTurnOff", {"name": "light.left"})]


def test_a_word_of_one_name_only_names_that_device():
    response, trace, _ = floors(
        "turn off the left light but turn on the right one",
        answer(action={"Left Light": "turn_off", "Right Light": "turn_on"}),
    )
    assert acts(response) == [
        ("HassTurnOff", {"name": "light.left"}),
        ("HassTurnOn", {"name": "light.right"}),
    ]
    assert "part of a name: light.right" in trace["steps"][0]["result"]


def test_a_word_of_a_lock_or_garage_door_names_nothing():
    rule = answer(action="open")
    response, *_ = floors("open the gate", rule)  # "Gate" is the name: said exactly, it counts
    assert acts(response) == [("HassTurnOn", {"name": "cover.gate"})]
    response, *_ = floors("open the gates now", rule)  # another ending, still the name
    assert acts(response) == [("HassTurnOn", {"name": "cover.gate"})]
    home = FLOORS.model_copy(
        update={"entities": [Entity(id="lock.front", name="Front Door Lock", area_id="garage")]}
    )
    response, *_ = run("unlock the front", home=home)  # one word of the lock's name
    assert response.reason == "no_target"


def test_a_short_word_is_not_another_ending_of_a_name():
    response, _, provider = run(
        "make it 23 degrees in here",
        answer(action="set_temperature"),
        satellite_area_id="living_room",
    )
    assert acts(response) == [
        ("HassClimateSetTemperature", {"name": "climate.living_room", "temperature": 23.0})
    ]  # not the Coffee Maker


def test_a_floor_name_with_another_ending():
    response, *_ = floors(
        "schließ alle Rollos im Obergeschosses",
        answer(action="close", of="cover"),
        "de",
    )
    assert {a[1]["area"] for a in acts(response)} == {"office", "kids"}


def test_endings_swap_but_short_or_different_words_do_not_match():
    from assist_decider_server.pipeline import _same

    assert _same("zweiten", "zweiter") and _same("linke", "linkes") and _same("licht", "lichter")
    assert not _same("bad", "bade") and not _same("hall", "halt") and not _same("room", "roomy1x")
