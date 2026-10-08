"""Turn a sentence plus the exposed home into Home Assistant intent calls.

The steps (BENCHMARK.md calls this "the best approach"; HOW-IT-WORKS.md explains it):
  1. Devices, by code: the device and room names said. Nothing named: the previous command's
     devices ("turn it off"), else the speaker's room.
  2. Model: is it a command or a question? Asked once per sentence.
  3. Model, per named room: which of its devices? Only devices that can take a spoken number
     are offered ("fit"). Locks and garage doors are never picked from a room.
  4. Model, per device: what should happen, from the actions its kind supports. On/off words
     rule out the opposite action; when both are said, each device follows the nearer one
     ("near").
  5. Model, per action with a value: which spoken number?
  6. Any answer below the confidence threshold hands the whole sentence back to Home Assistant.

Every step is recorded in a trace dict that the log UI renders.
"""

from __future__ import annotations

import hashlib
import time
import unicodedata
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from . import numbers
from .intents import ACTIONS, DOMAIN_ACTIONS, intent_for, is_sensitive
from .lang import LANGS, Lang, domain_words_in, fold, tokenize
from .protocol import Action, Home, ProcessRequest, ProcessResponse
from .providers import DecisionProvider, Question

MAX_DEVICES = 10  # per sentence: the protocol's limit on actions
MAX_OPTIONS = 10  # devices in one "which device?" question
MAX_MEMORY = 256  # contexts remembered for follow-ups
NONE = "none"


class HandOff(Exception):
    """The sentence goes back to Home Assistant (its fallback agent, or "Sorry, …")."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------- home index


@dataclass
class EntityRec:
    id: str
    domain: str
    name: str
    area_id: str | None
    device_class: str | None
    sensitive: bool
    names: dict[tuple[str, ...], str]  # folded words -> the name or alias as written


@dataclass
class AreaRec:
    id: str
    name: str
    names: list[tuple[str, ...]]


@dataclass
class Index:
    entities: dict[str, EntityRec]
    areas: dict[str, AreaRec]
    phrases: dict[tuple[str, ...], list[tuple[str, str]]]  # folded tokens -> [(kind, id)]
    longest: int
    domains: set[str]


def _phrase(text: str) -> tuple[str, ...]:
    return tuple(tokenize(fold(text)))


def build_index(home: Home) -> Index:
    entities, areas, phrases = {}, {}, {}
    for a in home.areas:
        rec = AreaRec(a.id, a.name, [p for p in map(_phrase, [a.name, *a.aliases]) if p])
        areas[a.id] = rec
        for p in rec.names:
            phrases.setdefault(p, []).append(("area", a.id))
    for e in home.entities:
        domain = e.id.split(".", 1)[0]
        names: dict[tuple[str, ...], str] = {}
        for text in [e.name, *e.aliases]:
            if p := _phrase(text):
                names.setdefault(p, text)
        rec = EntityRec(
            e.id,
            domain,
            e.name,
            e.area_id if e.area_id in areas else None,
            e.device_class,
            is_sensitive(domain, e.device_class),
            names,
        )
        entities[e.id] = rec
        for p in rec.names:
            if ("entity", e.id) not in phrases.setdefault(p, []):
                phrases[p].append(("entity", e.id))
    return Index(
        entities,
        areas,
        phrases,
        max((len(p) for p in phrases), default=1),
        {e.domain for e in entities.values()},
    )


_INDEX_CACHE: OrderedDict[str, Index] = OrderedDict()


def get_index(home: Home) -> Index:
    key = hashlib.sha256(home.model_dump_json().encode()).hexdigest()
    if key in _INDEX_CACHE:
        _INDEX_CACHE.move_to_end(key)
        return _INDEX_CACHE[key]
    index = _INDEX_CACHE[key] = build_index(home)
    if len(_INDEX_CACHE) > 8:
        _INDEX_CACHE.popitem(last=False)
    return index


# ---------------------------------------------------------------- names in the sentence


@dataclass
class Mention:
    start: int  # token index
    end: int
    refs: list[tuple[str, str]]  # [(kind, id)]


def find_mentions(tokens: list[str], index: Index) -> list[Mention]:
    """Greedy longest match of entity/area names; names inside names lose ("Kitchen Light")."""
    mentions, i = [], 0
    while i < len(tokens):
        for n in range(min(index.longest, len(tokens) - i), 0, -1):
            refs = index.phrases.get(tuple(tokens[i : i + n]))
            if refs:
                mentions.append(Mention(i, i + n, refs))
                i += n
                break
        else:
            # German compounds: "wohnzimmerlicht" mentions the area "wohnzimmer".
            refs = [
                ("area", a.id)
                for a in index.areas.values()
                if any(
                    len(p) == 1
                    and len(p[0]) >= 4
                    and tokens[i].startswith(p[0])
                    and tokens[i] != p[0]
                    for p in a.names
                )
            ]
            if refs:
                mentions.append(Mention(i, i + 1, refs))
            i += 1
    return mentions


# ---------------------------------------------------------------- follow-up memory

# The last command's devices per context (satellite or conversation): (time, entity IDs).
# ponytail: in-process and lost on restart, which is fine for a memory of about a minute.
_MEMORY: OrderedDict[str, tuple[float, list[str]]] = OrderedDict()


def recall(req: ProcessRequest) -> list[str]:
    if not req.context_id or not req.options.memory_seconds:
        return []
    entry = _MEMORY.get(req.context_id)
    if entry is None or time.monotonic() - entry[0] > req.options.memory_seconds:
        return []
    return entry[1]


def remember(req: ProcessRequest, entity_ids: list[str]) -> None:
    if not req.context_id or not entity_ids:
        return
    _MEMORY[req.context_id] = (time.monotonic(), entity_ids)
    _MEMORY.move_to_end(req.context_id)
    if len(_MEMORY) > MAX_MEMORY:
        _MEMORY.popitem(last=False)


# ---------------------------------------------------------------- decisions


def _confidence(probs: dict[str, float], choice: str) -> float:
    """Top probability rescaled so 0 means 'uniform guess' for any number of options."""
    n = len(probs)
    return 1.0 if n < 2 else max(0.0, (n * probs[choice] - 1) / (n - 1))


@dataclass
class Group:
    """The devices one name (or the speaker's room) can mean. Several: the model picks one."""

    devices: list[EntityRec]
    mention: Mention | None  # where it was said; None for the speaker's room or a follow-up
    source: str  # for the log UI


@dataclass
class Target:
    device: EntityRec
    mention: Mention | None
    called: str  # how the questions name the device


class Decider:
    """Decides one sentence. Raises HandOff when it should go back to Home Assistant."""

    def __init__(
        self, req: ProcessRequest, index: Index, provider: DecisionProvider, trace: dict[str, Any]
    ) -> None:
        self.req, self.index, self.provider, self.trace = req, index, provider, trace
        self.lang: Lang = LANGS[req.language]
        self.threshold = req.options.confidence_threshold
        self.model_ms = 0.0
        self.confidence = 1.0  # lowest of all answers so far
        folded = fold(req.text)
        # NFKC can lengthen text ("ﬀ" -> "ff"); the protocol caps the text at 500.
        self.text = unicodedata.normalize("NFKC", req.text)[:500]
        self.tokens = tokenize(folded)
        self.mentions = find_mentions(self.tokens, index)
        inside = {i for m in self.mentions for i in range(m.start, m.end)}
        self.free = [i for i in range(len(self.tokens)) if i not in inside]  # outside names
        self.nums = numbers.extract(" ".join(self.tokens[i] for i in self.free), req.language)
        trace["said"] = sorted({ref[1] for m in self.mentions for ref in m.refs})
        trace["numbers"] = [{"value": n.value, "unit": n.unit} for n in self.nums]

    def step(self, phase: str, check: str, result: str, ok: bool | None = None, **extra: Any):
        """One node of the decision tree in the log UI, in the order the checks ran.

        ok: True = this check decided, False = it failed, None = information only.
        """
        self.trace["steps"].append(
            {"phase": phase, "check": check, "result": result, "ok": ok, **extra}
        )

    def ask(
        self,
        phase: str,
        check: str,
        question: Question,
        allowed: list[str] | None = None,
        ids: dict[str, str] | None = None,
    ) -> str:
        """Ask the model one question about the sentence. Options outside `allowed` stay
        visible to the model (it is sensitive to option count and order) but are masked out
        of the answer. `ids` maps option keys to entity IDs, so the log UI can mark them."""
        started = time.perf_counter()
        answer = self.provider.predict({"utterance": self.text}, {"q": question}, self.req.language)
        elapsed = (time.perf_counter() - started) * 1000
        self.model_ms += elapsed
        probs = answer["q"].probs
        if allowed is not None:
            kept = {k: p for k, p in probs.items() if k in allowed}
            total = sum(kept.values()) or 1.0
            probs = {k: p / total for k, p in kept.items()}
        choice = max(probs, key=probs.__getitem__)
        conf = _confidence(probs, choice)
        self.confidence = min(self.confidence, conf)
        self.trace["questions"].append(
            {
                "instructions": question.instructions,
                "options": question.options,
                "probs": answer["q"].probs,
                "masked": sorted(set(answer["q"].probs) - set(probs)),
                "choice": choice,
                "confidence": round(conf, 3),
                "threshold": self.threshold,
                "ms": round(elapsed, 1),
                **({"ids": ids} if ids else {}),
            }
        )
        ok = conf >= self.threshold
        q = len(self.trace["questions"]) - 1
        self.step(phase, check, f"{question.options[choice]} ({conf:.2f})", ok, q=q)
        if not ok:
            raise HandOff("low_confidence")
        return choice

    # -- 1. which devices (code only)

    def find_groups(self) -> list[Group]:
        idx, sat = self.index, self.req.satellite_area_id
        said_areas = {r for m in self.mentions for k, r in m.refs if k == "area"}
        groups: list[Group] = []
        rooms: list[tuple[str, Mention]] = []
        for m in self.mentions:
            ents = [idx.entities[r] for k, r in m.refs if k == "entity"]
            if not ents:
                rooms += [(r, m) for k, r in m.refs if k == "area"]
                continue
            ents = [e for e in ents if e.domain in DOMAIN_ACTIONS]  # e.g. no vacuums yet
            if not ents:
                continue
            if len(ents) > 1:  # the same name in several rooms
                for label, areas in (("room said", said_areas), ("speaker's room", {sat})):
                    if narrowed := [e for e in ents if e.area_id in areas]:
                        self.step("devices", "same name in several rooms", f"kept by {label}")
                        ents = narrowed
                        break
            groups.append(Group(ents, m, "named"))
        named = {e.id for g in groups for e in g.devices}
        for area_id, m in rooms:
            # A room whose device is named ("the light in the kitchen") is already covered.
            inside = [e for e in idx.entities.values() if e.area_id == area_id]
            if any(e.id in named for e in inside):
                continue
            if devices := self.room_devices(area_id):
                groups.append(Group(devices, m, f"room {area_id}"))
        if groups:
            self.step("devices", "names said", self.describe(groups), True)
            return groups
        self.step("devices", "names said", "none usable" if self.mentions else "none", False)

        previous = [idx.entities[i] for i in recall(self.req) if i in idx.entities]
        if previous and not domain_words_in(self.tokens, self.lang):
            # "turn it off": nothing named and no kind of device said.
            self.step("devices", "previous command", ", ".join(e.id for e in previous), True)
            return [Group([e], None, "previous command") for e in previous]
        if previous:
            self.step("devices", "previous command", "a kind of device was said", False)

        if sat in idx.areas and (devices := self.room_devices(sat)):
            groups = [Group(devices, None, "speaker's room")]
            self.step("devices", "speaker's room", self.describe(groups), True)
            return groups
        self.step("devices", "speaker's room", "unknown" if sat is None else "no devices", False)
        raise HandOff("no_target")

    def room_devices(self, area_id: str) -> list[EntityRec]:
        """A room's devices the model may pick from: never locks or garage doors, and only
        those that can take a spoken number, if any can ("fit")."""
        devices = [
            e
            for e in self.index.entities.values()
            if e.area_id == area_id and not e.sensitive and e.domain in DOMAIN_ACTIONS
        ]
        fitting = [e for e in devices if self.fits(e)]
        if self.nums and fitting and len(fitting) < len(devices):
            self.step("devices", f"room {area_id}: fits the number", self.ids(fitting))
            devices = fitting
        if len(devices) > MAX_OPTIONS:  # big room: narrow down by the kind of device said
            kinds = domain_words_in(self.tokens, self.lang)
            devices = [e for e in devices if e.domain in kinds]
            self.step("devices", f"room {area_id}: kind said", self.ids(devices) or "none")
            if not devices or len(devices) > MAX_OPTIONS:
                raise HandOff("too_many_devices")
        return devices

    def fits(self, e: EntityRec) -> bool:
        """One of the device's actions takes one of the spoken numbers."""
        return any(
            (slot := ACTIONS[a].number) is not None and slot.accepts(n.value, n.unit)
            for a in DOMAIN_ACTIONS[e.domain]
            for n in self.nums
        )

    # -- 3. which device in a room

    def pick(self, group: Group) -> EntityRec:
        if len(group.devices) == 1:
            return group.devices[0]
        options = {e.id: entity_option(e, self.index, self.lang) for e in group.devices}
        question = Question(self.lang.which_question, options)
        choice = self.ask("devices", "which device", question, ids={k: k for k in options})
        return self.index.entities[choice]

    def called(self, e: EntityRec, group: Group) -> str:
        """The device's name or alias that was said, as written in Home Assistant."""
        m = group.mention
        if group.source == "named" and m is not None:
            return e.names.get(tuple(self.tokens[m.start : m.end]), e.name)
        return e.name

    # -- 4. and 5. what to do with each device

    def polarity(self, target: Target) -> tuple[bool, bool]:
        """(on, off): which kinds of on/off words were said, as they apply to this device."""
        lang = self.lang
        if target.device.domain == "lock":  # "ab"/"close" lock a lock, "auf"/"open" unlock it
            on_words, off_words = lang.lock_words, lang.unlock_words
        else:
            on_words, off_words = lang.on_words, lang.off_words
        on = [i for i in self.free if self.tokens[i] in on_words]
        off = [i for i in self.free if self.tokens[i] in off_words]
        m = target.mention
        if not (on and off and m):
            return bool(on), bool(off)

        # Both said: the nearer one counts ("near"); a tie keeps both.
        def distance(i: int) -> int:
            return m.start - i if i < m.start else i - m.end + 1

        nearest_on, nearest_off = min(map(distance, on)), min(map(distance, off))
        return nearest_on <= nearest_off, nearest_off <= nearest_on

    def action(self, target: Target, is_question: bool) -> tuple[str, float | int | None]:
        e = target.device
        offered = DOMAIN_ACTIONS[e.domain]
        on, off = self.polarity(target)
        allowed = [
            a
            for a in offered
            if (a == "query") == is_question
            and intent_for(a, e.domain) in self.req.intents
            and not (ACTIONS[a].polarity == "on" and off and not on)
            and not (ACTIONS[a].polarity == "off" and on and not off)
        ]
        if not allowed:
            self.step("action", e.id, "every action was ruled out", False)
            raise HandOff("no_action")
        question = Question(
            self.lang.action_question.format(device=target.called),
            {a: self.lang.actions[a] for a in offered},
        )
        choice = self.ask("action", e.id, question, allowed=allowed)
        slot = ACTIONS[choice].number
        if slot is None:
            return choice, None
        if not self.nums:
            self.step("action", e.id, "no number said", False)
            raise HandOff("missing_value")
        options = {f"{n.value:g}": f"{n.value:g}" for n in self.nums} | {NONE: self.lang.no_value}
        value = self.ask(
            "action", e.id, Question(self.lang.value_question.format(device=target.called), options)
        )
        if value == NONE:
            raise HandOff("missing_value")
        if not any(f"{n.value:g}" == value and slot.accepts(n.value, n.unit) for n in self.nums):
            self.step("action", e.id, f"{value} does not fit {slot.slot}", False)
            raise HandOff("value_not_possible")
        return choice, slot.clean(float(value))

    # -- all steps

    def decide(self) -> list[Action]:
        if any(self.tokens[i] in self.lang.condition_words for i in self.free):
            # ponytail: "if ..." is not supported; hand off rather than run it unguarded.
            raise HandOff("conditional")
        groups = self.find_groups()
        if len(groups) > MAX_DEVICES:
            raise HandOff("too_many_devices")
        kind = Question(self.lang.kind_question, self.lang.kinds)
        is_question = self.ask("kind", "command or question", kind) == "question"
        targets: dict[str, Target] = {}  # by entity ID: a device said twice acts once
        for g in groups:
            e = self.pick(g)
            targets.setdefault(e.id, Target(e, g.mention, self.called(e, g)))
        decided = [(t.device, *self.action(t, is_question)) for t in targets.values()]
        actions = []
        for e, action, value in decided:
            slots: dict[str, Any] = {"name": e.id}
            if value is not None:
                slots[ACTIONS[action].number.slot] = value  # type: ignore[union-attr]
            actions.append(
                Action(
                    intent=intent_for(action, e.domain),
                    slots=slots,
                    segment=self.text,
                    confidence=round(self.confidence, 3),
                )
            )
        return actions

    def describe(self, groups: list[Group]) -> str:
        return "; ".join(f"{g.source}: {self.ids(g.devices)}" for g in groups)

    @staticmethod
    def ids(devices: list[EntityRec]) -> str:
        return ", ".join(e.id for e in devices)


# ---------------------------------------------------------------- the log UI's Home tab


def kind_label(domain: str, lang: Lang) -> str:
    return lang.domains.get(domain, (domain.replace("_", " "), ()))[0]


def entity_option(e: EntityRec, idx: Index, lang: Lang) -> str:
    """How a device is worded as an option in the "which device?" question."""
    area = idx.areas.get(e.area_id) if e.area_id else None
    kind = kind_label(e.domain, lang)
    if area:
        return lang.entity_option.format(name=e.name, kind=kind, area=area.name)
    return lang.entity_option_no_area.format(name=e.name, kind=kind)


def describe_home(home: Home, language: str) -> dict[str, Any]:
    """The home as the name matcher sees it, for the log UI's Home tab."""
    idx, lang = get_index(home), LANGS[language]
    areas = []
    for a in home.areas:
        names = idx.areas[a.id].names
        areas.append(
            {
                "id": a.id,
                "name": a.name,
                "aliases": a.aliases,
                "floor_id": a.floor_id,
                "words": [" ".join(p) for p in names],
                # find_mentions: a one-word name of 4+ letters also matches inside compounds.
                "compound": [p[0] for p in names if len(p) == 1 and len(p[0]) >= 4],
            }
        )
    entities = []
    for x in home.entities:
        e = idx.entities[x.id]
        entities.append(
            {
                "id": e.id,
                "name": e.name,
                "aliases": x.aliases,
                "area_id": e.area_id,
                "domain": e.domain,
                "kind": kind_label(e.domain, lang),
                "device_class": e.device_class,
                "sensitive": e.sensitive,
                "words": [" ".join(p) for p in e.names],
                "option": entity_option(e, idx, lang),
                "actions": list(DOMAIN_ACTIONS.get(e.domain, ())),
            }
        )
    floors = [{"id": f.id, "name": f.name, "aliases": f.aliases} for f in home.floors]
    # Words for each kind of device: one said means "not the previous command's devices".
    kinds = {d: list(lang.domains.get(d, ("", ()))[1]) for d in sorted(idx.domains)}
    return {
        "language": language,
        "floors": floors,
        "areas": areas,
        "entities": entities,
        "kinds": kinds,
    }


# ---------------------------------------------------------------- entry point


def decide(
    req: ProcessRequest, provider: DecisionProvider
) -> tuple[ProcessResponse, dict[str, Any]]:
    started = time.perf_counter()
    trace_id = uuid.uuid4().hex[:12]
    trace: dict[str, Any] = {
        "type": "trace",
        "trace_id": trace_id,
        "ts": time.time(),
        "language": req.language,
        "text": req.text,
        "satellite_area": req.satellite_area_id,
        "steps": [],
        "questions": [],
    }
    actions: list[Action] = []
    reason = None
    decider = None
    try:
        if req.language not in provider.languages:
            raise HandOff("unsupported_language")
        index = get_index(req.home)
        if not index.entities:
            raise HandOff("no_exposed_entities")
        decider = Decider(req, index, provider, trace)
        actions = decider.decide()
        remember(req, [a.slots["name"] for a in actions])
    except HandOff as err:
        reason = err.reason
    elapsed = (time.perf_counter() - started) * 1000
    response = ProcessResponse(
        status="ok" if actions else "escalate",
        actions=actions,
        unresolved=[] if actions else [req.text],
        reason=reason,
        trace_id=trace_id,
        elapsed_ms=round(elapsed, 1),
    )
    trace.update(
        status=response.status,
        reason=reason,
        elapsed_ms=round(elapsed, 1),
        model_ms=round(decider.model_ms if decider else 0.0, 1),
        actions=[a.model_dump() for a in actions],
    )
    return response, trace
