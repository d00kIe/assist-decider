"""Turn a sentence plus the exposed home into Home Assistant intent calls.

No word lists: code finds the names the home has and the numbers said, and the model answers
short choice questions about the sentence (HOW-IT-WORKS.md explains it, BENCHMARK.md measures it).
  1. Code: the devices, rooms and floors named, give or take a word's ending ("das linke Licht"),
     and a word that is in only one device's name ("the right one"). Nothing named: the previous
     command's devices ("turn it off"), or the speaker's room, its floor or the whole home.
  2. Model, one call with every question that needs no other answer: command or question; is it
     about the home at all (else hand off); for a place: one device or all of a kind, which
     kind, which device; what to do with each device named.
  3. Model, second call: what to do with the devices picked from a place; then each device named
     among others again, with only its own words as the sentence. The two answers are averaged.
  4. Code: the value (the only spoken number that fits, or the one in the device's own words);
     "all" becomes one action per room (area + kind) unless a lock or garage door is in it.
  5. The least sure answer used must reach the threshold; locks and garage doors need more.

Every step is recorded in a trace dict that the log UI renders.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import unicodedata
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from . import numbers
from .intents import ACTIONS, DOMAIN_ACTIONS, NO_DOMAIN_SLOT, NumberSlot, intent_for, is_sensitive
from .lang import LANGS, TOKEN_RE, Lang, fold, tokenize
from .protocol import Action, Home, ProcessRequest, ProcessResponse
from .providers import DecisionProvider, Question

MAX_DEVICES = 10  # per sentence: the protocol's limit on actions
MAX_OPTIONS = 10  # devices in one "which device?" question
MAX_MEMORY = 256  # contexts remembered for follow-ups
NONE = "none"
# Locks and garage doors: acting on one needs every answer at least this sure, whatever the
# threshold set in Home Assistant. Reading their state doesn't.
SENSITIVE_MIN = 0.5
# Hand the sentence off when the model gives "not about the home" at least OTHER_AT. Chosen on
# the dev sentences so that no command there is handed off (BENCHMARK.md). Intern-Decision
# answers "not about the home" up to 0.76 for commands, depending on the other questions in the
# same call.
OTHER_AT = 0.8
# Nothing named: act on the speaker's whole floor or home only when the model says "all of a
# kind" and gives that place at least PLACE_AT. Widening acts in other rooms, and Laya answers
# "the whole home" for many plain sentences ("turn on the light"); otherwise it stays here.
PLACE_AT = 0.9


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
    floor_id: str | None


@dataclass
class Index:
    entities: dict[str, EntityRec]
    areas: dict[str, AreaRec]
    floors: dict[str, str]  # id -> name
    phrases: dict[tuple[str, ...], list[tuple[str, str]]]  # folded tokens -> [(kind, id)]
    stems: dict[tuple[str, ...], list[tuple[str, ...]]]  # each word's first 4 letters -> phrases
    owners: dict[str, set[tuple[str, str]]]  # a word of a name -> whose names have it
    longest: int
    domains: set[str]


def _phrase(text: str) -> tuple[str, ...]:
    return tuple(tokenize(fold(text)))


def _stems(words: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(w[:4] for w in words)


def _same(a: str, b: str) -> bool:
    """The same word, give or take an ending of up to two letters: "linke"/"linkes",
    "light"/"lights", "zweiten"/"zweiter". The start they share has 4 letters or more."""
    common = len(os.path.commonprefix([a, b]))
    return a == b or (common >= 4 and len(a) - common <= 2 and len(b) - common <= 2)


def build_index(home: Home) -> Index:
    entities, areas, phrases = {}, {}, {}
    floors = {f.id: f.name for f in home.floors}
    for f in home.floors:
        for p in filter(None, map(_phrase, [f.name, *f.aliases])):
            phrases.setdefault(p, []).append(("floor", f.id))
    for a in home.areas:
        names = [p for p in map(_phrase, [a.name, *a.aliases]) if p]
        rec = AreaRec(a.id, a.name, names, a.floor_id if a.floor_id in floors else None)
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
    stems: dict[tuple[str, ...], list[tuple[str, ...]]] = {}
    owners: dict[str, set[tuple[str, str]]] = {}
    for p, refs in phrases.items():
        stems.setdefault(_stems(p), []).append(p)
        for word in p:
            owners.setdefault(word, set()).update(refs)
    return Index(
        entities,
        areas,
        floors,
        phrases,
        stems,
        owners,
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
    refs: list[tuple[str, str]]  # [(kind, id)]: kind is "entity", "area" or "floor"
    partial: bool = False  # one word of the name ("the right one")


def find_mentions(tokens: list[str], index: Index) -> list[Mention]:
    """Greedy longest match of the names of devices, rooms and floors, give or take a word's
    ending ("das linke Licht" names "Linkes Licht"); names inside names lose ("Kitchen Light")."""
    mentions, i = [], 0
    while i < len(tokens):
        for n in range(min(index.longest, len(tokens) - i), 0, -1):
            words = tuple(tokens[i : i + n])
            refs = index.phrases.get(words) or list(
                dict.fromkeys(
                    ref
                    for p in index.stems.get(_stems(words), ())
                    if all(map(_same, words, p))
                    for ref in index.phrases[p]
                )
            )
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

# The last command per context (satellite or conversation): (time, entity IDs, sentence).
# ponytail: in-process and lost on restart, which is fine for a memory of about a minute.
_MEMORY: OrderedDict[str, tuple[float, list[str], str]] = OrderedDict()


def recall(req: ProcessRequest) -> tuple[list[str], str]:
    """The previous command's devices and sentence, if it was recent enough."""
    if not req.context_id or not req.options.memory_seconds:
        return [], ""
    entry = _MEMORY.get(req.context_id)
    if entry is None or time.monotonic() - entry[0] > req.options.memory_seconds:
        return [], ""
    return entry[1], entry[2]


def remember(req: ProcessRequest, entity_ids: list[str]) -> None:
    if not req.context_id or not entity_ids:
        return
    _MEMORY[req.context_id] = (time.monotonic(), entity_ids, req.text)
    _MEMORY.move_to_end(req.context_id)
    if len(_MEMORY) > MAX_MEMORY:
        _MEMORY.popitem(last=False)


# ---------------------------------------------------------------- decisions


def _confidence(probs: dict[str, float], choice: str) -> float:
    """Top probability rescaled so 0 means 'uniform guess' for any number of options."""
    n = len(probs)
    return 1.0 if n < 2 else max(0.0, (n * probs[choice] - 1) / (n - 1))


def _masked(probs: dict[str, float], allowed: list[str] | None) -> dict[str, float]:
    """`probs` over the allowed options only, scaled back up to 1."""
    if allowed is None:
        return probs
    kept = {k: p for k, p in probs.items() if k in allowed}
    total = sum(kept.values()) or 1.0
    return {k: p / total for k, p in kept.items()}


@dataclass
class Group:
    """The devices a name can mean. A place (a room, a floor, around the speaker) can mean one
    of its devices or all of one kind; a device name in several rooms means one of them."""

    devices: list[EntityRec]
    mention: Mention | None  # where it was said; None around the speaker or for a follow-up
    source: str  # for the log UI
    areas: list[str] = field(default_factory=list)  # a place's rooms; empty for a device name
    place: str = ""  # a place's name, for the log UI


@dataclass
class Target:
    device: EntityRec  # for "all": the one the questions name
    mention: Mention | None
    called: str  # how the questions name the device
    areas: list[str] = field(default_factory=list)  # "all": every device of its kind in these


class Decider:
    """Decides one sentence. Raises HandOff when it should go back to Home Assistant."""

    def __init__(
        self, req: ProcessRequest, index: Index, provider: DecisionProvider, trace: dict[str, Any]
    ) -> None:
        self.req, self.index, self.provider, self.trace = req, index, provider, trace
        self.lang: Lang = LANGS[req.language]
        self.threshold = req.options.confidence_threshold
        self.model_ms = 0.0
        self.confidence = 1.0  # lowest of the answers used so far
        # key -> (question, probabilities, ms of its call, what the model read)
        self.asked: dict[str, tuple[Question, dict[str, float], float, str]] = {}
        folded = fold(req.text)
        # NFKC can lengthen text ("ﬀ" -> "ff"); the protocol caps the text at 500.
        self.text = unicodedata.normalize("NFKC", req.text)[:500]
        self.state = {"utterance": self.text}
        self.tokens = tokenize(folded)
        # Where each token is in self.text, to quote a device's own words to the model. The
        # decimal comma becomes a point there too, so the counts match unless NFKC got in the way.
        spans = [m.span() for m in TOKEN_RE.finditer(re.sub(r"(?<=\d),(?=\d)", ".", self.text))]
        self.spans = spans if len(spans) == len(self.tokens) else None
        mentions = find_mentions(self.tokens, index)
        self.mentions = sorted(mentions + self.partials(mentions), key=lambda m: m.start)
        inside = {i for m in self.mentions for i in range(m.start, m.end)}
        self.free = [i for i in range(len(self.tokens)) if i not in inside]  # outside names
        self.nums = numbers.extract(" ".join(self.tokens[i] for i in self.free), req.language)
        self.acted: list[str] = []  # the entity IDs the actions change or ask about
        trace["said"] = sorted({ref[1] for m in self.mentions for ref in m.refs})
        trace["numbers"] = [{"value": n.value, "unit": n.unit} for n in self.nums]

    def partials(self, mentions: list[Mention]) -> list[Mention]:
        """A word that is in the name of one device and no other device, room or floor names
        that device ("the right one" for "Right Light"). Never a lock or garage door."""
        inside = {i for m in mentions for i in range(m.start, m.end)}
        found = []
        for i, word in enumerate(self.tokens):
            if i in inside or len(word) < 4 or word[0].isdigit():
                continue
            # Another ending only from 5 letters on: "make" is not "maker" ("make it warmer").
            owners = {
                owner
                for name_word, whose in self.index.owners.items()
                if word == name_word or (len(word) >= 5 and _same(word, name_word))
                for owner in whose
            }
            if len(owners) == 1 and (owner := owners.pop())[0] == "entity":
                e = self.index.entities[owner[1]]
                if not e.sensitive and e.domain in DOMAIN_ACTIONS:
                    found.append(Mention(i, i + 1, [owner], partial=True))
        return found

    def step(self, phase: str, check: str, result: str, ok: bool | None = None, **extra: Any):
        """One node of the decision tree in the log UI, in the order the checks ran.

        ok: True = this check decided, False = it failed, None = information only.
        """
        self.trace["steps"].append(
            {"phase": phase, "check": check, "result": result, "ok": ok, **extra}
        )

    def ask_all(self, questions: dict[str, Question], utterance: str | None = None) -> None:
        """Ask the model every question at once, about the sentence (or `utterance`): one call,
        so the questions can't see each other's answers. `choose` then reads the answers that
        are actually used; the rest (asked in case they're needed) never count."""
        if not questions:
            return
        state = self.state if utterance is None else {"utterance": utterance}
        started = time.perf_counter()
        if getattr(self.provider, "batch", True):
            answers = self.provider.predict(state, questions, self.req.language)
        else:  # a provider that answers worse when its questions share one prompt
            answers = {}
            for key, q in questions.items():
                answers |= self.provider.predict(state, {key: q}, self.req.language)
        elapsed = (time.perf_counter() - started) * 1000
        self.model_ms += elapsed
        for key, q in questions.items():
            self.asked[key] = (q, answers[key].probs, elapsed, state["utterance"])

    def record(self, key: str, masked: list[str], choice: str, conf: float, **extra: Any) -> int:
        """Put an answer in the trace for the log UI; returns its index."""
        question, raw, elapsed, utterance = self.asked[key]
        self.trace["questions"].append(
            {
                "key": key,
                "instructions": question.instructions,
                "options": question.options,
                "probs": raw,
                "masked": masked,
                "choice": choice,
                "confidence": round(conf, 3),
                "threshold": self.threshold,
                "ms": round(elapsed, 1),  # the whole call this question was part of
                **({"utterance": utterance} if utterance != self.text else {}),
                **extra,
            }
        )
        return len(self.trace["questions"]) - 1

    def says(self, key: str, option: str, at: float, check: str) -> bool:
        """Whether the model gives `option` at least `at`. For questions that only hand a
        sentence off: they don't count towards the confidence check."""
        question, raw, _, _ = self.asked[key]
        p = raw[option]
        q = self.record(key, [], max(raw, key=raw.__getitem__), _confidence(raw, option))
        self.step("request", check, f"{question.options[option]}: {p:.2f}", not p >= at, q=q)
        return p >= at

    def choose(
        self,
        phase: str,
        check: str,
        key: str,
        allowed: list[str] | None = None,
        ids: dict[str, str] | None = None,
        also: str | None = None,
    ) -> str:
        """Use the answer to question `key`. Options outside `allowed` stay visible to the
        model (it is sensitive to option count and order) but are masked out of the answer.
        `ids` maps option keys to entity IDs, so the log UI can mark them. `also` is the same
        question asked about other words: the mean of both answers decides, so a sure answer
        wins over an unsure one, and a disagreement lowers the confidence. Only answers that
        are used count for the confidence check at the end."""
        question, raw, _, _ = self.asked[key]
        probs = _masked(raw, allowed)
        if also:
            other = _masked(self.asked[also][1], allowed)
            pick = max(other, key=other.__getitem__)
            q = self.record(also, sorted(set(raw) - set(other)), pick, _confidence(other, pick))
            self.step(phase, f"{check}, its own words", f"{question.options[pick]}", q=q)
            probs = {k: (p + other[k]) / 2 for k, p in probs.items()}
        choice = max(probs, key=probs.__getitem__)
        conf = _confidence(probs, choice)
        self.confidence = min(self.confidence, conf)
        masked = sorted(set(raw) - set(probs))
        q = self.record(key, masked, choice, conf, **({"ids": ids} if ids else {}))
        ok = conf >= self.threshold
        self.step(phase, check, f"{question.options[choice]} ({conf:.2f})", ok, q=q)
        return choice

    def topics(self) -> dict[str, str]:
        """The topic options: "home" names the kinds of device this home has ("lights, blinds")."""
        kinds = dict.fromkeys(
            desc.split(", ")[0]
            for domain, (_, desc) in self.lang.domains.items()
            if domain in self.index.domains
        )
        return self.lang.topics | {"home": self.lang.topics["home"].format(kinds=", ".join(kinds))}

    # -- 1. which devices (code only)

    def find_groups(self) -> list[Group]:
        """The devices, rooms and floors said. Empty when nothing was named."""
        idx, sat = self.index, self.req.satellite_area_id
        said_areas = {r for m in self.mentions for k, r in m.refs if k == "area"}
        groups: list[Group] = []
        places: list[tuple[str, str, Mention]] = []
        for m in self.mentions:
            ents = [idx.entities[r] for k, r in m.refs if k == "entity"]
            if not ents:
                places += [(k, r, m) for k, r in m.refs if k in ("area", "floor")]
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
            groups.append(Group(ents, m, "part of a name" if m.partial else "named"))
        named_areas = said_areas | {e.area_id for g in groups for e in g.devices}
        for kind, ref, m in places:
            if kind == "area":
                # A room whose device is named ("the light in the kitchen") is already covered.
                if any(e.area_id == ref for g in groups for e in g.devices):
                    continue
                areas, name = [ref], idx.areas[ref].name
            else:
                areas = [a.id for a in idx.areas.values() if a.floor_id == ref]
                if named_areas & set(areas):  # "the office on the second floor"
                    continue
                name = idx.floors[ref]
            if g := self.place(areas, name, m, f"{kind} {ref}"):
                groups.append(g)
        result = self.describe(groups) if groups else "none usable" if self.mentions else "none"
        self.step("devices", "names said", result, bool(groups))
        return groups

    def place(
        self, areas: list[str], name: str, mention: Mention | None, source: str
    ) -> Group | None:
        """A place's devices the model may pick from: never locks or garage doors, and only
        those that can take a spoken number, if any can ("fit")."""
        devices = [
            e
            for e in self.index.entities.values()
            if e.area_id in areas and not e.sensitive and e.domain in DOMAIN_ACTIONS
        ]
        fitting = [e for e in devices if self.fitting(e)]
        if self.nums and fitting and len(fitting) < len(devices):
            self.step("devices", f"{name}: fits the number", self.ids(fitting))
            devices = fitting
        return Group(devices, mention, source, areas, name) if devices else None

    def fitting(
        self, e: EntityRec, slot: NumberSlot | None = None, nums: list[numbers.Num] | None = None
    ) -> list[float]:
        """The different spoken numbers that `slot` (else any of the device's) accepts."""
        slots = [slot] if slot else [ACTIONS[a].number for a in DOMAIN_ACTIONS[e.domain]]
        nums = self.nums if nums is None else nums
        return sorted({n.value for n in nums for s in slots if s and s.accepts(n.value, n.unit)})

    # -- 3. which device in a room

    def which_question(self, devices: list[EntityRec]) -> Question:
        options = {e.id: entity_option(e, self.index, self.lang) for e in devices}
        return Question(self.lang.which_question, options)

    def device_kind_question(self, kinds: list[str]) -> Question:
        options = {d: self.lang.domains.get(d, (d, d))[1] for d in kinds}
        return Question(self.lang.device_kind_question, options)

    def group_questions(self, i: int, g: Group, kinds: list[str]) -> dict[str, Question]:
        """Call 1 for one group: which kind and which device (a place), which device (a name in
        several rooms), or what to do with its only device."""
        out = {}
        if g.areas and len(kinds) > 1:
            out[f"kind:{i}"] = self.device_kind_question(kinds)
        if 1 < len(g.devices) <= MAX_OPTIONS:
            out[f"which:{i}"] = self.which_question(g.devices)
        elif len(g.devices) == 1:
            out |= self.action_questions(self.target(g.devices[0], g))
        return out

    def pick(self, key: str, group: Group) -> EntityRec:
        if len(group.devices) == 1:
            return group.devices[0]
        ids = {k: k for k in self.asked[key][0].options}
        return self.index.entities[self.choose("devices", "which device", key, ids=ids)]

    def target(self, e: EntityRec, group: Group) -> Target:
        return Target(e, group.mention, self.called(e, group))

    def called(self, e: EntityRec, group: Group) -> str:
        """The device's name or alias that was said, as written in Home Assistant."""
        m = group.mention
        if group.source == "named" and m is not None:
            return e.names.get(tuple(self.tokens[m.start : m.end]), e.name)
        return e.name

    # -- 4. and 5. what to do with each device

    def phrase(self, target: Target) -> tuple[int, int] | None:
        """The tokens about one device: halfway to the names said before and after it. Only
        positions, no word lists. None when fewer than two names were said."""
        m = target.mention
        if m is None or len(self.mentions) < 2:
            return None
        i = self.mentions.index(m)
        before, after = self.mentions[i - 1] if i else None, self.mentions[i + 1 :]
        lo = (before.end + m.start + 1) // 2 if before else 0
        hi = (m.end + after[0].start + 1) // 2 if after else len(self.tokens)
        return lo, hi

    def words(self, lo: int, hi: int) -> str:
        if self.spans:
            return self.text[self.spans[lo][0] : self.spans[hi - 1][1]]
        return " ".join(self.tokens[lo:hi])

    def values(self, target: Target, slot: NumberSlot | None = None) -> list[float]:
        """The numbers that could be the device's value: those that fit, narrowed to the ones in
        its own words when that leaves one ("the thermostat to 22 and the heating to 24")."""
        fitting = self.fitting(target.device, slot)
        if len(fitting) > 1 and (phrase := self.phrase(target)):
            free = set(self.free)
            own = " ".join(self.tokens[i] for i in range(*phrase) if i in free)
            mine = self.fitting(target.device, slot, numbers.extract(own, self.req.language))
            if len(mine) == 1 and mine[0] in fitting:
                return mine
        return fitting

    def action_questions(self, target: Target) -> dict[str, Question]:
        """What to do with one device; and which number, when two or more different spoken
        numbers could be its value (asked now in case the action takes one)."""
        e = target.device
        out = {
            f"action:{e.id}": Question(
                self.lang.action_question.format(device=target.called),
                {a: self.lang.actions[a] for a in DOMAIN_ACTIONS[e.domain]},
            )
        }
        if len(self.values(target)) > 1:
            options = {f"{n.value:g}": f"{n.value:g}" for n in self.nums}
            out[f"value:{e.id}"] = Question(
                self.lang.value_question.format(device=target.called),
                options | {NONE: self.lang.no_value},
            )
        return out

    def action(self, target: Target, is_question: bool) -> tuple[str, float | int | None]:
        e = target.device
        allowed = [
            a
            for a in DOMAIN_ACTIONS[e.domain]
            if (a == "query") == is_question and intent_for(a, e.domain) in self.req.intents
        ]
        if not allowed:
            self.step("action", e.id, "every action was ruled out", False)
            raise HandOff("no_action")
        own = f"own:{e.id}"
        choice = self.choose(
            "action", e.id, f"action:{e.id}", allowed, also=own if own in self.asked else None
        )
        slot = ACTIONS[choice].number
        if slot is None:
            return choice, None
        fitting = self.values(target, slot)
        if not fitting:
            self.step("action", e.id, f"no number said fits {slot.slot}", False)
            raise HandOff("value_not_possible" if self.nums else "missing_value")
        if len(fitting) == 1:  # no question: the model is unsure about obvious numbers
            self.step("action", e.id, f"{slot.slot} {fitting[0]:g}: the only number for it")
            return choice, slot.clean(fitting[0])
        value = self.choose("action", e.id, f"value:{e.id}")
        if value == NONE:
            raise HandOff("missing_value")
        if not any(f"{v:g}" == value for v in fitting):
            self.step("action", e.id, f"{value} does not fit {slot.slot}", False)
            raise HandOff("value_not_possible")
        return choice, slot.clean(float(value))

    # -- all steps

    def decide(self) -> list[Action]:
        lang, idx = self.lang, self.index
        groups = self.find_groups()
        if len(groups) > MAX_DEVICES:
            raise HandOff("too_many_devices")
        # Call 1: every question that needs no other answer.
        first = {
            "kind": Question(lang.kind_question, lang.kinds),
            "topic": Question(lang.topic_question, self.topics()),
        }
        previous: list[EntityRec] = []
        if not groups:  # nothing named: the previous command's devices, or around the speaker
            ids, sentence = recall(self.req)
            previous = [idx.entities[i] for i in ids if i in idx.entities]
            sat = self.req.satellite_area_id
            here = (
                self.place([sat], idx.areas[sat].name, None, "speaker's room")
                if sat in idx.areas
                else None
            )
            if here is None and not previous:
                result = "unknown" if sat is None else "no devices"
                self.step("devices", "speaker's room", result, False)
                raise HandOff("no_target")
            if previous and here:
                first["reference"] = Question(
                    lang.reference_question.format(previous=sentence), lang.references
                )
            if here:
                first["place"] = Question(lang.place_question, lang.places)
                groups = [here]
        # Around the speaker the place may grow to the floor or the home: every kind there.
        everywhere = sorted(
            {
                e.domain
                for e in idx.entities.values()
                if e.area_id and not e.sensitive and e.domain in DOMAIN_ACTIONS
            }
        )
        if "place" in first or any(g.areas and len(g.devices) > 1 for g in groups):
            first["scope"] = Question(lang.scope_question, lang.scopes)
        for i, g in enumerate(groups):
            kinds = everywhere if "place" in first else sorted({e.domain for e in g.devices})
            first |= self.group_questions(i, g, kinds)
        self.ask_all(first)
        if self.says("topic", "other", OTHER_AT, "about the home"):
            raise HandOff("not_for_home")
        is_question = self.choose("kind", "command or question", "kind") == "question"
        if previous or "place" in first:
            groups = self.around(previous, groups)
        targets = self.targets(groups)
        if is_question and any(t.areas for t in targets):
            # ponytail: "are all the lights off?" needs HA's any/all replies (PLAN.md, M2)
            self.step("action", "all of a kind", "a question about all of them", False)
            raise HandOff("unsupported")
        # Call 2: what to do with the devices picked from a place.
        self.ask_all(
            {
                key: q
                for t in targets
                for key, q in self.action_questions(t).items()
                if key not in self.asked
            }
        )
        # Then each device named among others again, with only its own words as the sentence
        # ("… und den Fernseher aus"): there the model binds on/off to the right device. The two
        # answers are combined in `choose`.
        for t in targets:
            if phrase := self.phrase(t):
                key = f"action:{t.device.id}"
                self.ask_all({f"own:{t.device.id}": self.asked[key][0]}, self.words(*phrase))
        decided = [(t, *self.action(t, is_question)) for t in targets]
        return self.compose(decided, self.check(decided))

    def around(self, previous: list[EntityRec], groups: list[Group]) -> list[Group]:
        """Nothing named: the previous command's devices, or the speaker's room, its floor or
        the whole home."""
        idx = self.index
        if previous and (
            not groups
            or self.choose("devices", "the previous command's devices", "reference") == "previous"
        ):
            self.step("devices", "previous command", self.ids(previous), True)
            return [Group([e], None, "previous command") for e in previous]
        place, scope = self.asked["place"][1], self.asked["scope"][1]
        floor = idx.areas[self.req.satellite_area_id].floor_id
        wider = "home" if floor is None or place["home"] > place["floor"] else "floor"
        if place[wider] < PLACE_AT or scope["all"] < scope["one"]:
            self.step("devices", "where", f"here ({wider} {place[wider]:.2f})")
            return groups
        where = self.choose("devices", "where", "place", [wider])
        areas = [a.id for a in idx.areas.values() if where == "home" or a.floor_id == floor]
        name = idx.floors[floor] if where == "floor" and floor else "home"
        if (g := self.place(areas, name, None, f"speaker's {where}")) is None:
            raise HandOff("no_target")
        return [g]

    def targets(self, groups: list[Group]) -> list[Target]:
        """One device per group; for a place, one device or all devices of one kind."""
        found: dict[str, Target] = {}  # by entity ID: a device said twice acts once
        scope = "one"
        if "scope" in self.asked and any(g.areas and len(g.devices) > 1 for g in groups):
            scope = self.choose("devices", "one or all", "scope")
        again: list[tuple[int, Group, list[EntityRec]]] = []
        for i, g in enumerate(groups):
            if not g.areas or len(g.devices) == 1:
                e = self.pick(f"which:{i}", g)
                found.setdefault(e.id, self.target(e, g))
                continue
            kinds = sorted({e.domain for e in g.devices})
            kind = kinds[0]
            if len(kinds) > 1:
                kind = self.choose("devices", f"which kind, {g.place}", f"kind:{i}", kinds)
            devices = [e for e in g.devices if e.domain == kind]
            asked = self.asked.get(f"which:{i}")
            if scope == "all":
                found[f"all:{i}"] = Target(devices[0], g.mention, devices[0].name, g.areas)
            elif len(devices) == 1:
                found.setdefault(devices[0].id, self.target(devices[0], g))
            elif asked and set(asked[0].options) == {e.id for e in g.devices}:
                e = self.pick(f"which:{i}", g)
                if e.domain != kind:  # the two answers contradict each other
                    self.step("devices", "which kind and which device", f"not a {kind}", False)
                    raise HandOff("inconsistent")
                found.setdefault(e.id, self.target(e, g))
            else:  # a big place, or the speaker's floor or home: which one of that kind
                again.append((i, g, devices))
        if any(len(devices) > MAX_OPTIONS for *_, devices in again):
            raise HandOff("too_many_devices")
        self.ask_all({f"which:{i}:again": self.which_question(d) for i, _, d in again})
        for i, g, devices in again:
            ids = {e.id: e.id for e in devices}
            e = self.index.entities[
                self.choose("devices", "which device", f"which:{i}:again", ids=ids)
            ]
            found.setdefault(e.id, self.target(e, g))
        return list(found.values())

    def compose(self, decided: list[tuple[Target, str, Any]], confidence: float) -> list[Action]:
        """The intent calls: a device by its ID; "all" as one call per room (area and kind),
        unless a lock or garage door of that kind is in the room: then the others by ID."""
        calls: list[tuple[str, dict[str, Any], list[str]]] = []  # (intent, slots, entity IDs)
        for t, action, value in decided:
            e = t.device
            intent = intent_for(action, e.domain)
            values = {} if value is None else {ACTIONS[action].number.slot: value}  # type: ignore[union-attr]
            if not t.areas:
                calls.append((intent, {"name": e.id, **values}, [e.id]))
                continue
            for area in t.areas:
                same = [
                    x
                    for x in self.index.entities.values()
                    if x.area_id == area and x.domain == e.domain
                ]
                usable = [x for x in same if not x.sensitive]
                if len(usable) < len(same):
                    calls += [(intent, {"name": x.id, **values}, [x.id]) for x in usable]
                elif usable:
                    where = {"area": area} | (
                        {} if intent in NO_DOMAIN_SLOT else {"domain": [e.domain]}
                    )
                    calls.append((intent, where | values, [x.id for x in usable]))
        if len(calls) > MAX_DEVICES:
            raise HandOff("too_many_devices")
        self.acted = [i for *_, ids in calls for i in ids]
        c = round(confidence, 3)
        return [Action(intent=i, slots=s, segment=self.text, confidence=c) for i, s, _ in calls]

    def check(self, decided: list[tuple[Target, str, Any]]) -> float:
        """The confidence check: the least sure answer used must reach the threshold. Changing a
        lock or garage door needs SENSITIVE_MIN too. Returns the confidence that was checked."""
        confidence = self.confidence
        if confidence < SENSITIVE_MIN and any(
            t.device.sensitive and action != "query" for t, action, _ in decided
        ):
            confidence = 0.0
        ok = confidence >= self.threshold
        self.step("check", "confidence", f"{confidence:.2f}, needs {self.threshold:.2f}", ok)
        if not ok:
            raise HandOff("low_confidence")
        return confidence

    def describe(self, groups: list[Group]) -> str:
        return "; ".join(f"{g.source}: {self.ids(g.devices)}" for g in groups)

    @staticmethod
    def ids(devices: list[EntityRec]) -> str:
        return ", ".join(e.id for e in devices)


# ---------------------------------------------------------------- the log UI's Home tab


def kind_label(domain: str, lang: Lang) -> str:
    return lang.domains.get(domain, (domain.replace("_", " "), ""))[0]


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
    return {"language": language, "floors": floors, "areas": areas, "entities": entities}


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
        remember(req, decider.acted)
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
        model=provider.model,
    )
    trace.update(
        status=response.status,
        reason=reason,
        elapsed_ms=round(elapsed, 1),
        model_ms=round(decider.model_ms if decider else 0.0, 1),
        actions=[a.model_dump() for a in actions],
    )
    return response, trace
