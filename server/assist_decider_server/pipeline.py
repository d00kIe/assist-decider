"""Turn an utterance plus the exposed home into ordered Home Assistant intent calls.

Per request:
  fold text -> find spoken entity/area names -> reject what cannot be expressed (conditions,
  negation, exceptions, schedules) -> split into commands -> per command: lexical guards ->
  intent question -> every number accounted for -> targets (exact names first, then the
  previous command, model only if needed) -> confidence gate -> slots.

A wrong action is worse than no action: whenever the words say more than the result would
do, the request is escalated and Home Assistant hands it to the fallback agent.

Every decision is recorded in a trace dict that the log UI renders.
"""

from __future__ import annotations

import difflib
import hashlib
import time
import unicodedata
import uuid
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from . import numbers
from .intents import INTENT_KEYS, INTENTS, IntentSpec, is_sensitive
from .lang import LANGS, Lang, domain_hits, fold, fold_with_map, tokenize
from .protocol import Action, Home, ProcessRequest, ProcessResponse
from .providers import DecisionProvider, Question

MAX_SEGMENTS = 5
MAX_FUZZY_WORDS = 20
MAX_OPTIONS = 10  # per question, including "none": keeps Laya calibrated and fast
NONE = "none"
MAX_MEMORY = 256  # contexts remembered for follow-ups


class Escalate(Exception):
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
    names: list[tuple[str, ...]]


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
    return tuple(t for t, _, _ in tokenize(fold(text)))


def build_index(home: Home) -> Index:
    entities, areas, phrases = {}, {}, {}
    for a in home.areas:
        rec = AreaRec(a.id, a.name, [p for p in map(_phrase, [a.name, *a.aliases]) if p])
        areas[a.id] = rec
        for p in rec.names:
            phrases.setdefault(p, []).append(("area", a.id))
    for e in home.entities:
        domain = e.id.split(".", 1)[0]
        rec = EntityRec(
            e.id,
            domain,
            e.name,
            e.area_id if e.area_id in areas else None,
            e.device_class,
            is_sensitive(domain, e.device_class),
            [p for p in map(_phrase, [e.name, *e.aliases]) if p],
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


# ---------------------------------------------------------------- utterance


@dataclass
class Mention:
    start: int  # token index
    end: int
    refs: list[tuple[str, str]]  # [(kind, id)]
    compound: bool = False  # an area inside a longer word: "wohnzimmerlicht"


@dataclass
class Segment:
    tokens: list[str]
    text: str  # original wording, for HA's text_input and the trace
    mentions: list[Mention]  # token indexes relative to `tokens`
    free: list[str] = field(default_factory=list)  # tokens outside mentions
    has_verb: bool = False
    polarity: frozenset[str] = frozenset()  # "on" / "off" words that are really on/off
    domains: set[str] = field(default_factory=set)  # device words outside spoken names
    domain_tokens: set[str] = field(default_factory=set)


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
                mentions.append(Mention(i, i + 1, refs, compound=True))
            i += 1
    return mentions


def _values(nums: list[numbers.Num]) -> list[numbers.Num]:
    """Numbers that are values. A bare 1 is usually the pronoun: "turn that one off"."""
    return [n for n in nums if n.unit or n.value != 1]


@dataclass
class Clause:
    """What one stretch between conjunctions says, read without the model."""

    start: int
    end: int
    has_verb: bool = False
    polarity: set[str] = field(default_factory=set)
    has_value: bool = False
    domains: set[str] = field(default_factory=set)
    domain_tokens: set[str] = field(default_factory=set)

    @property
    def evidence(self) -> bool:
        """It says by itself what to do: an on/off word or a value."""
        return bool(self.polarity) or self.has_value


def read_clause(
    tokens: list[str], start: int, end: int, mentions: list[Mention], lang: Lang, language: str
) -> Clause:
    clause = Clause(start, end)
    inside = {i for m in mentions for i in range(m.start, m.end)}
    compound = {m.start for m in mentions if m.compound}

    # Verbs. "switch" and "lock" are devices after an article, a name or another verb:
    # "the kitchen switch", "turn on the switch"; verbs otherwise: "switch it off".
    verbs: set[int] = set()
    for i in range(start, end):
        if i in inside or tokens[i] not in lang.verbs:
            continue
        noun = tokens[i] in lang.noun_verbs and (
            bool(verbs) or (i > start and (tokens[i - 1] in lang.articles or i - 1 in inside))
        )
        if not noun:
            verbs.add(i)
    clause.has_verb = bool(verbs)

    # Device words, outside spoken names: "the lights in the kitchen and the TV" asks for
    # lights, not for media players. Compounds keep theirs: "wohnzimmerlicht".
    for offset, domain in domain_hits(tokens[start:end], lang):
        i = start + offset
        if i in verbs or (i in inside and i not in compound):
            continue
        clause.domains.add(domain)
        clause.domain_tokens.add(tokens[i])

    # on/off. A particle is only on/off right after the verb ("turn on the light") or when
    # the clause or its object ends there ("mach das Licht an", "lights on in the kitchen").
    # Before an article, a name or a number it is a preposition: "the lamp on the desk",
    # "auf dem Flur", "auf 22 Grad".
    for i in range(start, end):
        tok = tokens[i]
        if i in inside or not (tok in lang.on_words or tok in lang.off_words):
            continue
        if tok in lang.noun_verbs and i not in verbs:
            continue  # "the front door lock" names the device, it does not say "lock"
        if tok in lang.particles:
            nxt = tokens[i + 1] if i + 1 < end else None
            ends_here = nxt is None or (
                i + 1 not in inside
                and (nxt == "?" or nxt in lang.stop_words - lang.articles - lang.particles)
            )
            if not (ends_here or i - 1 in verbs):
                continue
        clause.polarity.add("on" if tok in lang.on_words else "off")

    free = " ".join(tokens[i] for i in range(start, end) if i not in inside)
    clause.has_value = bool(_values(numbers.extract(free, language)))
    return clause


def split(
    tokens: list[str],
    spans: list[tuple[int, int]],
    text: str,
    mentions: list[Mention],
    lang: Lang,
    language: str,
) -> list[Segment]:
    """Split at conjunctions, but only where the next clause is a command of its own.

    "turn off the kitchen and the hallway lights" stays one command with two targets;
    "turn off the light and set the heating to 21" becomes two (its own verb), and so do
    "kitchen light on and desk lamp off" and "set the lamp to 50 percent and the spots to
    20 percent" (its own on/off word or value, when the command before has one too).
    """
    inside = {i for m in mentions for i in range(m.start, m.end)}
    cuts: list[tuple[int, int]] = []  # (conjunction start, conjunction end)
    i = 0
    while i < len(tokens):
        for conj in lang.conjunctions:
            if i not in inside and tuple(tokens[i : i + len(conj)]) == conj:
                cuts.append((i, i + len(conj)))
                i += len(conj)
                break
        else:
            i += 1
    bounds, start = [], 0
    for cut_start, cut_end in cuts:
        bounds.append((start, cut_start))
        start = cut_end
    bounds.append((start, len(tokens)))

    merged: list[list[Clause]] = []
    for seg_start, seg_end in bounds:
        if seg_end <= seg_start:
            continue
        clause = read_clause(tokens, seg_start, seg_end, mentions, lang, language)
        # No verb and nothing of its own to say: more targets for the command before
        # ("... and the hallway"). German puts a shared on/off word last ("das Licht in
        # der Küche und im Flur aus"), so evidence only splits when both sides have some.
        if (
            not merged
            or clause.has_verb
            or (clause.evidence and any(c.evidence for c in merged[-1]))
        ):
            merged.append([clause])
        else:
            merged[-1].append(clause)

    segments = []
    for clauses in merged:
        seg_start, seg_end = clauses[0].start, clauses[-1].end
        seg_mentions = [
            Mention(m.start - seg_start, m.end - seg_start, m.refs, m.compound)
            for m in mentions
            if seg_start <= m.start < seg_end
        ]
        segments.append(
            Segment(
                tokens=tokens[seg_start:seg_end],
                # NFKC can lengthen text ("ﬀ" -> "ff"); the protocol caps segments at 500.
                text=text[spans[seg_start][0] : spans[seg_end - 1][1]].strip(" ,;")[:500],
                mentions=seg_mentions,
                free=[tokens[j] for j in range(seg_start, seg_end) if j not in inside],
                has_verb=any(c.has_verb for c in clauses),
                polarity=frozenset().union(*(c.polarity for c in clauses)),
                domains=set().union(*(c.domains for c in clauses)),
                domain_tokens=set().union(*(c.domain_tokens for c in clauses)),
            )
        )
    return [s for s in segments if s.free or s.mentions]


def blocked(tokens: list[str], inside: set[int], lang: Lang) -> tuple[str, str] | None:
    """(reason, word) when the sentence says something no intent call can express."""
    for i, tok in enumerate(tokens):
        if i in inside:
            continue
        for reason, words in lang.blockers.items():
            if tok in words:
                return reason, tok
    return None


# ---------------------------------------------------------------- decisions


def _confidence(probs: dict[str, float], choice: str) -> float:
    """Max probability rescaled so 0 means 'uniform guess' for any number of options."""
    n = len(probs)
    return 1.0 if n < 2 else max(0.0, (n * probs[choice] - 1) / (n - 1))


@dataclass
class Target:
    kind: str  # "entity" | "area" | "none" (let HA pick)
    id: str | None = None
    domain: str | None = None


# The last command per context (satellite or conversation): (time, intent, targets).
# ponytail: in-process and lost on restart, which is fine for a memory of about a minute.
_MEMORY: OrderedDict[str, tuple[float, IntentSpec, list[Target]]] = OrderedDict()


def recall(req: ProcessRequest) -> tuple[IntentSpec, list[Target]] | None:
    if not req.context_id or not req.options.memory_seconds:
        return None
    entry = _MEMORY.get(req.context_id)
    if entry is None or time.monotonic() - entry[0] > req.options.memory_seconds:
        return None
    return entry[1], entry[2]


def remember(req: ProcessRequest, last: tuple[IntentSpec, list[Target]] | None) -> None:
    if not req.context_id or last is None:
        return
    _MEMORY[req.context_id] = (time.monotonic(), *last)
    _MEMORY.move_to_end(req.context_id)
    if len(_MEMORY) > MAX_MEMORY:
        _MEMORY.popitem(last=False)


class SegmentDecider:
    def __init__(
        self,
        req: ProcessRequest,
        index: Index,
        provider: DecisionProvider,
        trace: dict[str, Any],
        previous: tuple[IntentSpec, list[Target]] | None = None,
    ) -> None:
        self.req, self.index, self.provider = req, index, provider
        self.previous = previous
        self.lang = LANGS[req.language]
        self.threshold = req.options.confidence_threshold
        self.trace = trace
        self.model_ms = 0.0

    def ask(
        self, key: str, question: Question, state: dict[str, Any], allowed: set[str] | None = None
    ) -> str:
        """Ask one question. Options outside `allowed` stay visible to the model (it is
        sensitive to option count and order) but are masked out of the answer."""
        started = time.perf_counter()
        answer = self.provider.predict(state, {key: question}, self.req.language)[key]
        elapsed = (time.perf_counter() - started) * 1000
        self.model_ms += elapsed
        probs = answer.probs
        if allowed is not None:
            kept = {k: p for k, p in probs.items() if k in allowed}
            total = sum(kept.values()) or 1.0
            probs = {k: p / total for k, p in kept.items()}
        choice = max(probs, key=probs.__getitem__)
        conf = _confidence(probs, choice)
        self.trace["questions"].append(
            {
                "key": key,
                "instructions": question.instructions,
                "options": question.options,
                "probs": answer.probs,
                "masked": sorted(set(answer.probs) - set(probs)),
                # How much of the model's answer the guards removed: high values are a
                # guard and the model disagreeing. Recorded for tuning, not acted on.
                "masked_mass": round(1 - sum(answer.probs[k] for k in probs), 3),
                "choice": choice,
                "confidence": round(conf, 3),
                "threshold": self.threshold,
                "ms": round(elapsed, 1),
            }
        )
        if choice == NONE:
            raise Escalate("none_chosen" if key == "intent" else "no_target")
        if conf < self.threshold:
            raise Escalate("low_confidence")
        return choice

    # -- intent

    def candidate_intents(
        self, seg: Segment, nums: list[numbers.Num]
    ) -> tuple[list[IntentSpec], list[IntentSpec]]:
        """(intents shown to the model, subset allowed by the lexical guards)."""
        lang, words = self.lang, set(seg.free)
        # "is the light on?" is a question. "can you turn on the light?" is a command that
        # speech-to-text ends with "?": a question mark only counts without a command word.
        # Words that also describe a state are no command words: "garage door open?".
        command_words = (lang.verbs | lang.on_words | lang.off_words) - lang.question_words
        command_words -= lang.particles | lang.state_words
        question = bool(seg.free and seg.free[0] in lang.question_words) or (
            "?" in words and not words & command_words
        )
        on, off = "on" in seg.polarity, "off" in seg.polarity
        unit_value = any(n.unit in ("pct", "deg") for n in nums)
        # A device named by its exact name can only take the intents its kind supports:
        # "set the kitchen light to 20" is never a thermostat command.
        named = [
            [self.index.entities[r[1]] for r in m.refs if r[0] == "entity"]
            for m in seg.mentions
            if all(r[0] == "entity" for r in m.refs) and not self.everyday_name(seg, m)
        ]
        dropped: dict[str, str] = {}
        shown, keep = [], []
        for name, spec in INTENTS.items():
            if name not in self.req.intents:
                dropped[name] = "not registered in Home Assistant"
                continue
            if spec.domains is not None and not (spec.domains & self.index.domains):
                dropped[name] = "no exposed device"
                continue
            shown.append(spec)
            if any(not any(self.compatible(spec, e) for e in ents) for ents in named):
                dropped[name] = "does not fit the named device"
            elif spec.number and _pick_number(spec, nums) is None:
                dropped[name] = "no value spoken"
            elif question and not spec.query:
                dropped[name] = "utterance is a question"
            elif not question and spec.query and any(n.unit for n in nums):
                dropped[name] = "value spoken"
            elif spec.polarity == "on" and off and not on:
                dropped[name] = "'off' word spoken"
            elif spec.polarity == "off" and on and not off:
                dropped[name] = "'on' word spoken"
            elif spec.polarity and unit_value:
                dropped[name] = "value with unit spoken"
            else:
                keep.append(spec)
        self.trace["dropped_intents"] = dropped
        return shown, keep

    def everyday_name(self, seg: Segment, m: Mention) -> bool:
        """A name made only of device words ("Temperature", "Heating") may be meant as the
        word: "set the temperature to 22" is not about a sensor called Temperature."""
        tokens = seg.tokens[m.start : m.end]
        device_words = {i for i, _domain in domain_hits(tokens, self.lang)}
        generic = self.lang.temperature_words | self.lang.stop_words
        return all(i in device_words or t in generic for i, t in enumerate(tokens))

    def follow_up(self, seg: Segment, nums: list[numbers.Num]) -> IntentSpec | None:
        """ "and the kitchen too", "23 degrees": no verb of its own, so the previous intent.

        The model is not asked here, so every word must be accounted for: "I am going to
        the bedroom now" names a room too, and is not a command.
        """
        if not self.previous:
            return None
        spec = self.previous[0]
        if seg.has_verb or seg.polarity or set(seg.free) & self.lang.question_words:
            return None
        if spec.number is None and nums or spec.number and _pick_number(spec, nums) is None:
            return None
        if not seg.mentions and not spec.number:
            return None  # "thanks" must not repeat the previous command
        if self.unexplained(seg):
            return None
        return spec

    def unexplained(self, seg: Segment, verbs: bool = False) -> list[str]:
        """Words outside names that are no number, device word, on/off word or filler.
        With `verbs`, command verbs count as explained too ("turn it off")."""
        lang = self.lang
        known = lang.stop_words | lang.particles | lang.filler_words | lang.joiners
        known |= lang.on_words | lang.off_words | seg.domain_tokens
        if verbs:
            known |= lang.verbs - lang.question_words
        rest = numbers.strip(" ".join(seg.free), self.req.language).split()
        return [t for t in rest if t.isalnum() and t not in known]

    def spoken_polarity(self, seg: Segment, allowed: list[IntentSpec]) -> IntentSpec | None:
        """ "kitchen light on", "Licht im Flur aus": no verb, one on/off word and nothing
        else to interpret. The word decides; the model has nothing to add."""
        if seg.has_verb or len(seg.polarity) != 1 or self.unexplained(seg):
            return None
        (polarity,) = seg.polarity
        return next((s for s in allowed if s.polarity == polarity), None)

    def decide_intent(self, seg: Segment, nums: list[numbers.Num]) -> IntentSpec:
        if spec := self.follow_up(seg, nums):
            self.trace["intent_shortcut"] = "previous command"
            return spec
        value_words = set(seg.free) & self.lang.value_words
        if value_words and not any(n.unit in ("pct", "") for n in nums):
            self.trace["note"] = f"'{', '.join(sorted(value_words))}' without a value"
            raise Escalate("missing_value")
        shown, allowed = self.candidate_intents(seg, nums)
        if not allowed:
            raise Escalate("no_intent")
        if query := self.question_about_device(seg, allowed):
            self.trace["intent_shortcut"] = "question about a device"
            return query
        if not nums and (spec := self.spoken_polarity(seg, allowed)):
            self.trace["intent_shortcut"] = "spoken on/off word"
            return spec
        options = {s.key: self.lang.intents[s.name] for s in shown} | {NONE: self.lang.none_intent}
        choice = self.ask(
            "intent",
            Question(self.lang.intent_question, options),
            {"utterance": seg.text},
            allowed={s.key for s in allowed} | {NONE},
        )
        return INTENT_KEYS[choice]

    def question_about_device(self, seg: Segment, allowed: list[IntentSpec]) -> IntentSpec | None:
        """Laya cannot tell "is the light on?" from "turn the light on", but a question that
        names a device can only be a state query (read-only, so a wrong guess is harmless)."""
        if any(not spec.query for spec in allowed):
            return None  # not a question
        names_device = bool(seg.mentions) or bool(seg.domains)
        by_name = {spec.name: spec for spec in allowed}
        if set(seg.tokens) & self.lang.temperature_words and "HassClimateGetTemperature" in by_name:
            return by_name["HassClimateGetTemperature"]
        if names_device and "HassGetState" in by_name:
            return by_name["HassGetState"]
        return None

    # -- targets

    def compatible(self, spec: IntentSpec, e: EntityRec) -> bool:
        return spec.domains is None or e.domain in spec.domains

    def target_domain(self, t: Target) -> str | None:
        if t.kind == "entity":
            e = self.index.entities.get(t.id or "")
            return e.domain if e else None
        return t.domain

    def is_sensitive(self, t: Target) -> bool:
        e = self.index.entities.get(t.id or "") if t.kind == "entity" else None
        return e is not None and e.sensitive

    def target_fits(self, spec: IntentSpec, t: Target) -> bool:
        if t.kind == "entity":
            e = self.index.entities.get(t.id or "")
            return e is not None and self.compatible(spec, e)
        if t.kind == "area":
            return (
                not spec.entity_only
                and t.id in self.index.areas
                and (t.domain is None or spec.domains is None or t.domain in spec.domains)
            )
        return spec.target_optional

    def area_targets(self, spec: IntentSpec, area_ids: list[str], seg: Segment) -> list[Target]:
        if spec.entity_only:
            raise Escalate("area_not_supported")

        def usable(domains: Iterable[str | None]) -> set[str]:
            return {d for d in domains if d and (spec.domains is None or d in spec.domains)} - {
                "lock"
            }

        domains = usable(seg.domains)
        if not domains and spec.implied_domain:
            domains = {spec.implied_domain}
        if not domains:  # "the kitchen light and the bedroom": the kind of device named
            domains = usable(
                self.index.entities[r[1]].domain
                for m in seg.mentions
                for r in m.refs
                if r[0] == "entity"
            )
        if not domains and self.previous:  # "and the kitchen too": the previous kind of device
            domains = usable(self.target_domain(t) for t in self.previous[1])
        if not domains:
            if spec.target_optional:
                return [Target("area", a) for a in area_ids]
            raise Escalate("area_without_domain")
        return [Target("area", a, d) for a in area_ids for d in sorted(domains)]

    def qualifies(self, seg: Segment, area: Mention, entity: Mention) -> bool:
        """The room only says where the named device is: "the ceiling light in the bedroom"."""
        first, second = sorted((area, entity), key=lambda m: m.start)
        between = seg.tokens[first.end : second.start]
        return all(t in self.lang.stop_words and t not in self.lang.joiners for t in between)

    def decide_targets(self, spec: IntentSpec, seg: Segment) -> list[Target]:
        idx = self.index
        sat_area = self.req.satellite_area_id
        mentioned_areas = [r[1] for m in seg.mentions for r in m.refs if r[0] == "area"]
        targets: list[Target] = []
        named: list[tuple[Mention, EntityRec]] = []
        area_mentions: list[Mention] = []
        for m in seg.mentions:
            ents = [idx.entities[r[1]] for r in m.refs if r[0] == "entity"]
            ents = [e for e in ents if self.compatible(spec, e)]
            if not ents:
                if any(r[0] == "area" for r in m.refs):
                    area_mentions.append(m)
                elif self.everyday_name(seg, m):
                    continue
                else:
                    # A spoken name is never dropped to make the rest of the sentence fit.
                    raise Escalate("incompatible_target")
            if len(ents) > 1:  # same name in several rooms
                for area in (mentioned_areas, [sat_area]):
                    narrowed = [e for e in ents if e.area_id in area]
                    if narrowed:
                        ents = narrowed
                        break
            if len(ents) == 1:
                targets.append(Target("entity", ents[0].id))
            elif len(ents) > 1:
                targets.append(self.ask_target(spec, seg, [(e.id, 1.0) for e in ents], []))
            if ents:
                named.append((m, idx.entities[targets[-1].id or ""]))
        areas = list(
            dict.fromkeys(
                r[1]
                for m in area_mentions
                for r in m.refs
                if r[0] == "area"
                and not any(e.area_id == r[1] and self.qualifies(seg, m, em) for em, e in named)
            )
        )
        if areas:
            targets += self.area_targets(spec, areas, seg)
        if targets:
            self.trace["shortcut"] = "spoken names"
            return targets
        if self.previous and not seg.domains and not self.unexplained(seg, verbs=True):
            # "turn it off": nothing named, so the devices of the previous command. Locks
            # and garage doors are left out: they only act when named.
            kept = [
                t
                for t in self.previous[1]
                if self.target_fits(spec, t) and not self.is_sensitive(t)
            ]
            if kept:
                self.trace["shortcut"] = "previous command"
                return kept

        # Nothing named: a single matching device, the satellite's room, or ask the model.
        domains = seg.domains or ({spec.implied_domain} if spec.implied_domain else set())
        pool = [
            e for e in idx.entities.values() if self.compatible(spec, e) and e.domain in domains
        ]
        if len(pool) == 1 and not pool[0].sensitive:
            self.trace["shortcut"] = "only matching device"
            return [Target("entity", pool[0].id)]
        if pool and sat_area and not spec.entity_only:
            self.trace["shortcut"] = "satellite area"
            return self.area_targets(spec, [sat_area], seg)

        ents, area_scores = self.fuzzy(spec, seg)
        if not ents and not area_scores:
            if spec.target_optional:
                self.trace["shortcut"] = "let Home Assistant choose"
                return [Target("none")]
            raise Escalate("no_target")
        return [self.ask_target(spec, seg, ents, area_scores)]

    def fuzzy(
        self, spec: IntentSpec, seg: Segment
    ) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
        """Rank non-sensitive entities and areas by how well their names match the words."""
        # Bounded work: a spoken command has a handful of words; more is not a command.
        words = list(
            dict.fromkeys(w for w in seg.tokens if w.isalpha() and w not in self.lang.stop_words)
        )
        words = words[:MAX_FUZZY_WORDS]
        if not words:
            return [], []
        domains = seg.domains

        def name_score(names: list[tuple[str, ...]]) -> float:
            return max(
                (sum(max(_similar(t, w) for w in words) for t in n) / len(n) for n in names),
                default=0.0,
            )

        ents = []
        for e in self.index.entities.values():
            if e.sensitive or not self.compatible(spec, e):
                continue
            score = name_score(e.names)
            if score > 0:
                score += 0.3 * (e.domain in domains) + 0.2 * (
                    e.area_id == self.req.satellite_area_id
                )
                ents.append((e.id, score))
        areas = []
        if not spec.entity_only:
            areas = [(a.id, s) for a in self.index.areas.values() if (s := name_score(a.names)) > 0]
        ents.sort(key=lambda x: -x[1])
        areas.sort(key=lambda x: -x[1])
        self.trace["fuzzy"] = {"entities": ents[:12], "areas": areas[:6]}
        return ents, areas

    def ask_target(
        self,
        spec: IntentSpec,
        seg: Segment,
        ents: list[tuple[str, float]],
        areas: list[tuple[str, float]],
    ) -> Target:
        ranked = sorted(
            [("e", i, s) for i, s in ents] + [("a", i, s) for i, s in areas], key=lambda x: -x[2]
        )
        ranked = ranked[: MAX_OPTIONS - 1]
        options, keys = {}, {}
        lang, idx = self.lang, self.index
        for n, (kind, ident, _) in enumerate(ranked, 1):
            key = f"{kind}{n}"
            keys[key] = (kind, ident)
            if kind == "e":
                e = idx.entities[ident]
                label = lang.domains.get(e.domain, (e.domain.replace("_", " "), ()))[0]
                area = idx.areas.get(e.area_id) if e.area_id else None
                options[key] = (
                    lang.entity_option.format(name=e.name, kind=label, area=area.name)
                    if area
                    else lang.entity_option_no_area.format(name=e.name, kind=label)
                )
            else:
                options[key] = lang.area_option.format(area=idx.areas[ident].name)
        options[NONE] = lang.none_target
        state = {"utterance": seg.text}
        if self.req.satellite_area_id in idx.areas:
            state["room"] = idx.areas[self.req.satellite_area_id].name
        kind, ident = keys[self.ask("target", Question(lang.target_question, options), state)]
        if kind == "e":
            return Target("entity", ident)
        return self.area_targets(spec, [ident], seg)[0]


@lru_cache(maxsize=65536)
def _similar(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if min(len(a), len(b)) >= 4 and (a in b or b in a):
        return 0.8
    matcher = difflib.SequenceMatcher(None, a, b)
    if matcher.real_quick_ratio() < 0.8 or matcher.quick_ratio() < 0.8:
        return 0.0
    ratio = matcher.ratio()
    return ratio * 0.7 if ratio >= 0.8 else 0.0


def _slot_number(spec: IntentSpec, nums: list[numbers.Num]) -> numbers.Num | None:
    """The first spoken number the intent's slot accepts."""
    slot = spec.number
    if slot is None:
        return None
    return next((n for n in nums if n.unit in slot.units and slot.lo <= n.value <= slot.hi), None)


def _pick_number(spec: IntentSpec, nums: list[numbers.Num]) -> float | int | None:
    n = _slot_number(spec, nums)
    if n is None or spec.number is None:
        return None
    return round(n.value) if spec.number.integer else round(n.value * 2) / 2


def _unused_numbers(spec: IntentSpec, nums: list[numbers.Num]) -> list[numbers.Num]:
    """Values the intent has no slot for: "in 10 minutes", "at 7", a second percentage."""
    rest = _values(nums)
    used = _slot_number(spec, nums)
    if used in rest:
        rest.remove(used)
    return rest


def build_slots(spec: IntentSpec, target: Target, nums: list[numbers.Num]) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    if target.kind == "entity":
        slots["name"] = target.id
    elif target.kind == "area":
        slots["area"] = target.id
        if target.domain and spec.domain_slot:
            slots["domain"] = [target.domain]
    if spec.number:
        value = _pick_number(spec, nums)
        if value is None:
            raise Escalate("missing_value")
        slots[spec.number.slot] = value
    return slots


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
        "segments": [],
    }
    actions: list[Action] = []
    unresolved: list[str] = []
    reason = None
    model_ms = 0.0
    try:
        if req.language not in provider.languages:
            raise Escalate("unsupported_language")
        index = get_index(req.home)
        if not index.entities:
            raise Escalate("no_exposed_entities")
        folded, char_map = fold_with_map(req.text)
        toks = tokenize(folded)
        nfkc_text = unicodedata.normalize("NFKC", req.text)
        tokens = [t for t, _, _ in toks]
        # Map token spans back to the original wording for the per-command text.
        spans = [(char_map[s], char_map[e - 1] + 1) for _, s, e in toks]
        lang = LANGS[req.language]
        mentions = find_mentions(tokens, index)
        inside = {i for m in mentions for i in range(m.start, m.end)}

        # "if it is cold ...", "don't ...", "all except ...", "... tomorrow": running the
        # rest of the sentence as if the word were not there does the wrong thing.
        if found := blocked(tokens, inside, lang):
            trace["blocked"] = {"reason": found[0], "word": found[1]}
            raise Escalate(found[0])

        segments = split(tokens, spans, nfkc_text, mentions, lang, req.language)
        if len(segments) > MAX_SEGMENTS:
            raise Escalate("too_many_segments")
        memory = recall(req)
        trace["previous"] = memory[0].name if memory else None
        last: tuple[IntentSpec, list[Target]] | None = None
        for seg in segments:
            seg_trace: dict[str, Any] = {"text": seg.text, "questions": [], "actions": []}
            trace["segments"].append(seg_trace)
            # "turn on the light and then dim it": "it" is the command just before, in this
            # sentence first, in the previous one otherwise.
            decider = SegmentDecider(req, index, provider, seg_trace, last or memory)
            try:
                free_text = " ".join(seg.free)
                nums = numbers.extract(free_text, req.language)
                seg_trace["numbers"] = [{"value": n.value, "unit": n.unit} for n in nums]
                spec = decider.decide_intent(seg, nums)
                if unused := _unused_numbers(spec, nums):
                    seg_trace["note"] = "no use for " + ", ".join(
                        f"{n.value:g} {n.unit}".strip() for n in unused
                    )
                    raise Escalate("unused_number")
                targets = decider.decide_targets(spec, seg)
                built = []
                for target in targets:
                    slots = build_slots(spec, target, nums)
                    conf = min((q["confidence"] for q in seg_trace["questions"]), default=1.0)
                    built.append(
                        Action(intent=spec.name, slots=slots, segment=seg.text, confidence=conf)
                    )
                seg_trace["actions"] = [a.model_dump() for a in built]
                actions += built
                last = (spec, targets)
            except Escalate as err:
                seg_trace["escalate"] = err.reason
                unresolved.append(seg.text)
                reason = reason or err.reason
            finally:
                model_ms += decider.model_ms
        remember(req, last)
        if not segments:
            raise Escalate("no_intent")
    except Escalate as err:
        reason = err.reason
        unresolved = [req.text]
        actions = []
    elapsed = (time.perf_counter() - started) * 1000
    response = ProcessResponse(
        status="ok" if actions else "escalate",
        actions=actions[:10],
        unresolved=unresolved[:10],
        reason=reason,
        trace_id=trace_id,
        elapsed_ms=round(elapsed, 1),
    )
    trace.update(
        status=response.status,
        reason=reason,
        elapsed_ms=round(elapsed, 1),
        model_ms=round(model_ms, 1),
        actions=[a.model_dump() for a in response.actions],
    )
    return response, trace
