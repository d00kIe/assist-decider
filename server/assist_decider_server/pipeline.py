"""Turn an utterance plus the exposed home into ordered Home Assistant intent calls.

Per request:
  fold text -> find spoken entity/area names -> split into commands -> per command:
  lexical guards -> intent question -> targets (exact names first, then the previous turn,
  model only if needed) -> confidence gate -> slots.
  "if ..." commands are not supported: they escalate as a whole.

Every decision is recorded in a trace dict that the log UI renders.
"""

from __future__ import annotations

import difflib
import hashlib
import time
import unicodedata
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from . import numbers
from .intents import INTENT_KEYS, INTENTS, IntentSpec, is_sensitive
from .lang import LANGS, Lang, domain_words_in, fold, fold_with_map, tokenize
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


@dataclass
class Segment:
    tokens: list[str]
    text: str  # original wording, for HA's text_input and the trace
    mentions: list[Mention]
    free: list[str] = field(default_factory=list)  # tokens outside mentions


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


def split(
    tokens: list[str], spans: list[tuple[int, int]], text: str, mentions: list[Mention], lang: Lang
) -> list[Segment]:
    """Split at conjunctions, but only where the next clause brings its own verb.

    "turn off the kitchen and the hallway lights" stays one command with two targets;
    "turn off the light and set the heating to 21" becomes two.
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
        bounds.append((start, cut_start, cut_end))
        start = cut_end
    bounds.append((start, len(tokens), len(tokens)))

    merged: list[list[int]] = []  # [start, end]
    for seg_start, seg_end, _ in bounds:
        has_verb = any(
            tokens[j] in lang.verbs for j in range(seg_start, seg_end) if j not in inside
        )
        if merged and not has_verb:
            merged[-1][1] = seg_end
        elif seg_end > seg_start:
            merged.append([seg_start, seg_end])

    segments = []
    for seg_start, seg_end in merged:
        seg_mentions = [m for m in mentions if seg_start <= m.start < seg_end]
        seg_inside = {i for m in seg_mentions for i in range(m.start, m.end)}
        segments.append(
            Segment(
                tokens=tokens[seg_start:seg_end],
                # NFKC can lengthen text ("ﬀ" -> "ff"); the protocol caps segments at 500.
                text=text[spans[seg_start][0] : spans[seg_end - 1][1]].strip(" ,;")[:500],
                mentions=seg_mentions,
                free=[tokens[j] for j in range(seg_start, seg_end) if j not in seg_inside],
            )
        )
    return [s for s in segments if s.free or s.mentions]


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

    def step(
        self, phase: str, check: str, result: str, ok: bool | None = None, **extra: Any
    ) -> None:
        """One node of the decision tree in the log UI, in the order the checks ran.

        ok: True = this check decided, False = tried and passed over (or failed), None = info.
        """
        self.trace["steps"].append(
            {"phase": phase, "check": check, "result": result, "ok": ok, **extra}
        )

    def ask(
        self,
        key: str,
        question: Question,
        state: dict[str, Any],
        allowed: set[str] | None = None,
        ids: dict[str, str] | None = None,
    ) -> str:
        """Ask one question. Options outside `allowed` stay visible to the model (it is
        sensitive to option count and order) but are masked out of the answer.

        `ids` maps option keys to entity/area IDs, so the log UI can show them on the home."""
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
                "choice": choice,
                "confidence": round(conf, 3),
                "threshold": self.threshold,
                "ms": round(elapsed, 1),
                **({"ids": ids} if ids else {}),
            }
        )
        self.step(
            key,
            "Laya",
            f"{choice if key == 'intent' else question.options[choice]} ({conf:.2f})",
            choice != NONE and conf >= self.threshold,
            q=len(self.trace["questions"]) - 1,
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
        question = "?" in words or bool(seg.free and seg.free[0] in lang.question_words)
        on_words, off_words = lang.on_words, lang.off_words
        if any(
            kind == "entity" and self.index.entities[rid].domain == "lock"
            for m in seg.mentions
            for kind, rid in m.refs
        ):  # HassTurnOn locks, HassTurnOff unlocks
            on_words, off_words = lang.lock_words, lang.unlock_words
        on, off = bool(words & on_words), bool(words & off_words)
        unit_value = any(n.unit in ("pct", "deg") for n in nums)
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
            if spec.number and _pick_number(spec, nums) is None:
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
        self.step(
            "intent",
            "word rules",
            "allowed: " + (", ".join(s.key for s in keep) or "nothing"),
            None if keep else False,
            dropped=dropped,
        )
        return shown, keep

    def follow_up(self, seg: Segment, nums: list[numbers.Num]) -> IntentSpec | None:
        """ "and the kitchen too", "23 degrees": no verb of its own, so the previous intent."""
        if not self.previous:
            return None
        lang, spec = self.lang, self.previous[0]
        if set(seg.free) & (lang.verbs | lang.on_words | lang.off_words | lang.question_words):
            return None
        if spec.number is None and nums or spec.number and _pick_number(spec, nums) is None:
            return None
        if not seg.mentions and not spec.number:
            return None  # "thanks" must not repeat the previous command
        return spec

    def decide_intent(self, seg: Segment, nums: list[numbers.Num]) -> IntentSpec:
        if spec := self.follow_up(seg, nums):
            self.step("intent", "follow-up without a verb", f"reuse {spec.name}", True)
            return spec
        if self.previous:
            self.step("intent", "follow-up without a verb", "no", False)
        value_words = set(seg.free) & self.lang.value_words
        if value_words and not any(n.unit in ("pct", "") for n in nums):
            self.step(
                "intent", "value word", f"'{', '.join(sorted(value_words))}' without a value", False
            )
            raise Escalate("missing_value")
        shown, allowed = self.candidate_intents(seg, nums)
        if not allowed:
            raise Escalate("no_intent")
        if query := self.question_about_device(seg, allowed):
            self.step("intent", "question naming a device", query.name, True)
            return query
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
        names_device = bool(seg.mentions) or bool(domain_words_in(seg.tokens, self.lang))
        by_name = {spec.name: spec for spec in allowed}
        # Words outside names: "Outdoor Temperature" is a sensor, not a thermostat question.
        if set(seg.free) & self.lang.temperature_words and "HassClimateGetTemperature" in by_name:
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
        domains = domain_words_in(seg.tokens, self.lang)
        domains = {d for d in domains if spec.domains is None or d in spec.domains} - {"lock"}
        source = "said"
        if not domains and spec.implied_domain:
            domains, source = {spec.implied_domain}, "implied by the action"
        if not domains and self.previous:  # "and the kitchen too": the previous kind of device
            domains = {
                d
                for t in self.previous[1]
                if (d := self.target_domain(t)) and (spec.domains is None or d in spec.domains)
            } - {"lock"}
            source = "previous command"
        if not domains:
            if spec.target_optional:
                self.step("target", "kind of device", "not needed for this action")
                return [Target("area", a) for a in area_ids]
            self.step("target", "kind of device", "none said", False)
            raise Escalate("area_without_domain")
        self.step("target", "kind of device", f"{', '.join(sorted(domains))} ({source})")
        return [Target("area", a, d) for a in area_ids for d in sorted(domains)]

    def decide_targets(self, spec: IntentSpec, seg: Segment) -> list[Target]:
        idx = self.index
        sat_area = self.req.satellite_area_id
        mentioned_areas = [r[1] for m in seg.mentions for r in m.refs if r[0] == "area"]
        targets: list[Target] = []
        area_mentions: list[str] = []
        for m in seg.mentions:
            ents = [idx.entities[r[1]] for r in m.refs if r[0] == "entity"]
            ents = [e for e in ents if self.compatible(spec, e)]
            if not ents:
                area_mentions += [r[1] for r in m.refs if r[0] == "area"]
            if len(ents) > 1:  # same name in several rooms
                for label, area in (("room said", mentioned_areas), ("satellite room", [sat_area])):
                    narrowed = [e for e in ents if e.area_id in area]
                    if narrowed:
                        self.step(
                            "target",
                            "same name in several rooms",
                            f"kept {len(narrowed)} of {len(ents)} by {label}",
                        )
                        ents = narrowed
                        break
            if len(ents) == 1:
                targets.append(Target("entity", ents[0].id))
            elif len(ents) > 1:
                targets.append(self.ask_target(spec, seg, [(e.id, 1.0) for e in ents], []))
        entity_areas = {idx.entities[t.id].area_id for t in targets}
        areas = list(dict.fromkeys(a for a in area_mentions if a not in entity_areas))
        if areas:
            targets += self.area_targets(spec, areas, seg)
        if targets:
            self.step("target", "names said", _describe(targets), True)
            return targets
        self.step("target", "names said", "none usable" if seg.mentions else "none", False)
        if self.previous and not domain_words_in(seg.tokens, self.lang):
            # "turn it off": nothing named, so the devices of the previous command.
            kept = [t for t in self.previous[1] if self.target_fits(spec, t)]
            self.step(
                "target", "previous command's devices", _describe(kept) or "none fit", bool(kept)
            )
            if kept:
                return kept

        # Nothing named: a single matching device, the satellite's room, or ask the model.
        domains = domain_words_in(seg.tokens, self.lang) or (
            {spec.implied_domain} if spec.implied_domain else set()
        )
        pool = [
            e for e in idx.entities.values() if self.compatible(spec, e) and e.domain in domains
        ]
        if len(pool) == 1 and not pool[0].sensitive:
            self.step("target", "only device of that kind", pool[0].id, True)
            return [Target("entity", pool[0].id)]
        kinds = ", ".join(sorted(domains)) or "no kind said"
        self.step("target", "only device of that kind", f"{len(pool)} found ({kinds})", False)
        if pool and sat_area and not spec.entity_only:
            targets = self.area_targets(spec, [sat_area], seg)
            self.step("target", "satellite's room", _describe(targets), True)
            return targets
        if pool:
            reason = "action needs one device" if sat_area else "satellite has no room"
            self.step("target", "satellite's room", reason, False)

        ents, area_scores = self.fuzzy(spec, seg)
        if not ents and not area_scores:
            if spec.target_optional:
                self.step("target", "let Home Assistant choose", "no device needed", True)
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
        domains = domain_words_in(seg.tokens, self.lang)

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
        top = sorted(ents[:5] + areas[:3], key=lambda x: -x[1])
        self.step(
            "target",
            "similar names",
            ", ".join(f"{i} {s:.2f}" for i, s in top) or "none",
            None if top else False,
            scores={i: round(s, 2) for i, s in ents[:20] + areas[:10]},
        )
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
                options[key] = entity_option(idx.entities[ident], idx, lang)
            else:
                options[key] = lang.area_option.format(area=idx.areas[ident].name)
        options[NONE] = lang.none_target
        state = {"utterance": seg.text}
        if self.req.satellite_area_id in idx.areas:
            state["room"] = idx.areas[self.req.satellite_area_id].name
        ids = {key: ident for key, (_, ident) in keys.items()}
        question = Question(lang.target_question, options)
        kind, ident = keys[self.ask("target", question, state, ids=ids)]
        if kind == "e":
            return Target("entity", ident)
        return self.area_targets(spec, [ident], seg)[0]


def kind_label(domain: str, lang: Lang) -> str:
    return lang.domains.get(domain, (domain.replace("_", " "), ()))[0]


def entity_option(e: EntityRec, idx: Index, lang: Lang) -> str:
    """How an entity is worded as an answer in Laya's "which device?" question."""
    area = idx.areas.get(e.area_id) if e.area_id else None
    kind = kind_label(e.domain, lang)
    if area:
        return lang.entity_option.format(name=e.name, kind=kind, area=area.name)
    return lang.entity_option_no_area.format(name=e.name, kind=kind)


def describe_home(home: Home, language: str) -> dict[str, Any]:
    """The home as the matcher sees it, for the log UI's Home tab."""
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
                "option": lang.area_option.format(area=a.name),
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
            }
        )
    floors = [{"id": f.id, "name": f.name, "aliases": f.aliases} for f in home.floors]
    # Words that say which kind of device a room command means ("the kitchen lights").
    kinds = {d: list(lang.domains.get(d, ("", ()))[1]) for d in sorted(idx.domains)}
    return {
        "language": language,
        "floors": floors,
        "areas": areas,
        "entities": entities,
        "kinds": kinds,
    }


def _describe(targets: list[Target]) -> str:
    """Targets as the log UI shows them: "light.kitchen, room bedroom (light)"."""
    parts = []
    for t in targets:
        if t.kind == "entity":
            parts.append(str(t.id))
        elif t.kind == "area":
            parts.append(f"room {t.id}" + (f" ({t.domain})" if t.domain else ""))
        else:
            parts.append("Home Assistant chooses")
    return ", ".join(parts)


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


def _pick_number(spec: IntentSpec, nums: list[numbers.Num]) -> float | int | None:
    slot = spec.number
    if slot is None:
        return None
    for n in nums:
        if n.unit in slot.units and slot.lo <= n.value <= slot.hi:
            return round(n.value) if slot.integer else round(n.value * 2) / 2
    return None


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
        trace["said"] = sorted({ref[1] for m in mentions for ref in m.refs})
        # ponytail: "if ..." is not supported; escalate rather than run the command unguarded.
        inside = {i for m in mentions for i in range(m.start, m.end)}
        if any(t in lang.condition_words for i, t in enumerate(tokens) if i not in inside):
            raise Escalate("conditional")
        segments = split(tokens, spans, nfkc_text, mentions, lang)
        if len(segments) > MAX_SEGMENTS:
            raise Escalate("too_many_segments")
        previous = recall(req)
        trace["previous"] = previous[0].name if previous else None
        last: tuple[IntentSpec, list[Target]] | None = None
        for seg in segments:
            seg_trace: dict[str, Any] = {
                "text": seg.text,
                "questions": [],
                "steps": [],
                "actions": [],
            }
            trace["segments"].append(seg_trace)
            decider = SegmentDecider(req, index, provider, seg_trace, previous)
            try:
                free_text = " ".join(seg.free)
                nums = numbers.extract(free_text, req.language)
                seg_trace["numbers"] = [{"value": n.value, "unit": n.unit} for n in nums]
                spec = decider.decide_intent(seg, nums)
                targets = decider.decide_targets(spec, seg)
                for target in targets:
                    slots = build_slots(spec, target, nums)
                    conf = min((q["confidence"] for q in seg_trace["questions"]), default=1.0)
                    action = Action(
                        intent=spec.name, slots=slots, segment=seg.text, confidence=conf
                    )
                    seg_trace["actions"].append(action.model_dump())
                    actions.append(action)
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
