# Assist Decider: plan

What the project is, how it is built today, and what comes next. Tick boxes as work lands.
Measurements are in [BENCHMARK.md](BENCHMARK.md); how the pipeline works is in
[HOW-IT-WORKS.md](HOW-IT-WORKS.md).

## Goal

A Home Assistant voice-command agent powered by small "choice" models (Laya,
Intern-Decision, Liquid AI d1), running on a separate machine. It needs to be:

- fast and resident in memory
- English and German
- able to handle compound commands
- spoken answers
- a live log UI
- strong security
- configurable from Home Assistant
- publishable on HACS

## Architecture

- **Server** (`server/`, Python ≥3.11, FastAPI):
  - Holds one decision model, resident and warmed up.
  - Pipeline (`pipeline.py`), no word lists. Code finds names (devices, rooms, floors; other
    word endings like "linke"/"Linkes"; a word only one device's name has, never for locks or
    garage doors) and numbers. The model answers choice questions: in one call, command or
    question, about the home or not, conditional or not (two questions), and for a place one
    or all of a kind, which kind, which device; in a second call, what to do with each device.
    A device named among others is asked again with only its own words, and the two answers
    are averaged. A number that is the only one fitting the action is bound in code. "All"
    becomes one `area` + `domain` action per room, or per-device names where a lock or garage
    door of that kind is in the room. The confidence check counts only the answers used;
    changing a lock or garage door needs every answer at 0.5 or more.
  - Conditions: an "if/wenn …" sentence is handed off (`conditional`) when both condition
    questions say so at 0.7 or more. Home Assistant sends no device states.
  - Follow-ups: the last command's devices and sentence per `context_id` (hashed satellite
    device or conversation id), for `memory_seconds` (default 60). When nothing is named, the
    model says whether the previous command's devices are meant ("turn it off"); else here,
    the speaker's floor or the whole home (widening needs "all" and 0.9 or more).
  - Live log: in-memory ring buffer, server-sent events (SSE) over `fetch`, and a static UI.
- **Integration** (`custom_components/assist_decider/`, no pip requirements):
  - Pushes a names-only snapshot of exposed entities to the server.
  - Validates the returned actions and executes them in order via `intent.async_handle`
    with `assistant="conversation"`.
  - Speech comes from home-assistant-intents templates. There is an optional fallback agent.
- **Protocol**: `protocol.py` v3, byte-identical in both places, pydantic v2.
- **Provider seam**: `DecisionProvider.predict(state, questions, lang)` in `providers.py`
  answers several choice questions per call (Laya: one forward pass, answers independent;
  Intern-Decision: one prompt, answers can shift with the other questions). A provider can
  set `batch = False` to be asked one question at a time. A generative model would replace
  `pipeline.decide()` instead.

### Facts the design rests on

- The models only choose between options: no numbers, no splitting, no free text. Numbers
  and names stay in code.
- Models get worse when options are removed. Every question shows all options of that kind
  of device; ruled-out ones are masked afterwards.
- Option keys and wording matter. Choice questions with options that say what they cover
  work; Laya answers "yes" to almost any yes/no question.
- Laya and torch can't run inside HA OS (no musllinux wheels), hence the separate server.
- HA's built-in intent handlers return empty speech, so the integration renders
  home-assistant-intents templates. Response keys differ per language (`_response_keys`).
- Don't construct `ToolResultContent` in the chat log (2026.9 → 2026.10 API break). Only a
  final `AssistantContent` is added.

---

## Milestones

### M0: Server ✅

- [x] uv project, pinned dependencies, CLI `assist-decider` (serve, download)
- [x] Settings: defaults < TOML < `ASSIST_DECIDER_*` env < CLI; validation
- [x] `protocol.py` v3 (strict requests, lenient responses, length and ID limits)
- [x] Providers: Laya (multilingual, English), Intern-Decision 0.8B/2B, d1-3B, d1-omni-600M;
      pinned revisions, safetensors only, no remote code, warm-up, device auto
- [x] Numbers EN/DE: digits, words (unicode-rbnf), decimal comma, `komma`/`point`, %, °,
      durations, halves/quarters
- [x] Pipeline as above: names, rooms, floors, "all", follow-ups, confidence check, traces
- [x] No auth (closed home network); body limit, security headers/CSP, no docs,
      single-worker inference + 503, SSE log stream
- [x] Live log UI (vanilla JS, textContent only)
- [x] Benchmark (`tests/eval/benchmark.py`): 65 dev + 46 held-out test sentences

### M1: HA integration ✅

- [x] manifest, hacs.json (min HA 2026.9.0), brand icon, config flow (URL/TLS), reconfigure,
      options (thresholds, follow-up memory, fallback, no self-loop)
- [x] Setup: handshake, NotReady / repair issue on protocol mismatch
- [x] Conversation entity: snapshot of exposed entities only, satellite area, action
      validation (intent allowlist, exposed IDs, slot keys), sequential execution, template
      speech EN/DE, HA-native error wording, natural numbers ("21,5"), fallback agent, chat log
- [x] Diagnostics, strings + en/de translations
- [x] Tests with pytest-homeassistant-custom-component (HA 2026.9.4)

### M2: Language and coverage (next)

- [ ] German "… aus" at the end of a one-device sentence ("mach das Licht im Flur aus") is
      read as "on" by most models. Ideas: a third view of the words after the device's
      name; a fine-tune.
- [ ] Conditions and times ("turn on the coffee maker at 7") are caught only in part.
- [ ] Chit-chat ("tell me a joke", "play some jazz") still switches a device in the
      speaker's room with some models, mostly at low confidence.
- [ ] "The whole house" without a speaker's room (typed in the app) is handed off.
- [ ] **Yes/no state answers**: send a `state` slot ("an"/"on" → `on`) and use the
      `one_yesno` template. Currently German says "Bed light ist on".
- [ ] Questions about all of a kind ("are all the lights off?") → `any`/`all` replies;
      handed off today (`unsupported`)
- [ ] More intents: HassFanSetSpeed, HassSetVolume, media pause/unpause/next/previous,
      HassStartTimer/HassCancelTimer/HassTimerStatus (durations are already parsed),
      HassGetCurrentTime/Date, HassNevermind, HassStopMoving
- [ ] Brightness phrases (max/min/half), cover "halb"/"half" → 50
- [ ] Generative planner as an optional provider for long compounds and conditions (a
      1.5–3B model with JSON-constrained output), if they keep failing. Not done so far for
      speed and the 4 GB card.
- [ ] Check each `_response_keys` choice against the intents JSON in a test, for every
      supported intent × language
- [ ] Log UI: per-segment collapse, copy-as-test-case button

### M3: HACS readiness

- [ ] Review the pipeline (adversarial EN/DE inputs) and the integration against HA source
      (hassfest/HACS validation, API usage)
- [ ] Check on a real satellite that consecutive wake-word turns share the device id the
      follow-up memory is keyed on
- [ ] Create the GitHub repo `d00kIe/assist-decider` (description, topics: `home-assistant`,
      `hacs`, `assist`, `voice`, `laya`)
- [ ] Push, confirm CI (ci.yaml, validate.yaml: hassfest + HACS action) is green
- [ ] Dark brand variants (`dark_icon.png`), `logo.png`
- [ ] Exception translations (`translation_key` on raised errors)
- [ ] First release v0.1.0 (release.yaml attaches `assist_decider.zip`)
- [ ] Later: PR to `hacs/default`

### M4: Accuracy and calibration

- [ ] More benchmark sentences, ideally real ones from the live log
- [ ] Tune the default thresholds in HA per model (today 0.4 EN / 0.5 DE for every model)
- [ ] Optional: per-language temperature fit, document fine-tuning

### M5: Production deployment

- [ ] Linux + NVIDIA: verify the install, document the torch CUDA index for older GPUs,
      measure VRAM and speed on the 4 GB GeForce
- [ ] Windows + NVIDIA: verify the CUDA torch install command in the README
- [x] Ship `deploy/`: compose file without a Dockerfile (source at a pinned commit, packages
      from `uv.lock`), launchd plist; systemd unit and Windows scheduled task in the README
- [ ] Caddy TLS example
- [ ] CI: use the CPU torch index on Linux runners (smaller downloads)
- [ ] Publish the server to PyPI (trusted publishing), so `uv tool install assist-decider`
      works

### Later / ideas

- [ ] Wyoming "intent" mode (no custom integration needed; needs an HA token to read exposure)
- [ ] More providers: ONNX/CoreML Laya, NLI zero-shot (mDeBERTa), cross-encoder rerankers,
      GLiNER for free-text slots
- [ ] Choose or switch the server model from HA (admin endpoint)
- [ ] Hash-only context with resync (fewer bytes per request)
- [ ] State-aware disambiguation ("the light that is on")
- [ ] Light colors and kelvin; free-text intents (shopping list, broadcast, media search)
- [ ] Option to redact utterances from logs

---

## How to run

```bash
cd server && uv sync && uv run pytest && uv run pytest -m slow -s    # server
cd .. && uv sync --python 3.14 && uv run pytest tests                # integration
# Real HA dev instance (config in .ha-config/, git-ignored):
uv run hass -c .ha-config   # http://127.0.0.1:8124, integration symlinked in .ha-config/custom_components
```

On a Mac, Intern-Decision runs without its fast kernels (they are NVIDIA only), and Metal can
stall for minutes compiling graphs. Benchmark one model per process.
