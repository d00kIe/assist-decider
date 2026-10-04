# Assist Decider: plan and progress

The source of truth for what is done and what comes next. Tick boxes as work lands, and
add measurements and decisions so the next session can pick up without re-research.

## Goal

A Home Assistant voice-command agent powered by Laya (and later other "choice" models),
running on a separate machine. It needs to be:

- fast and resident in memory
- English and German
- able to handle compound commands
- spoken answers
- a live log UI
- strong security
- configurable from Home Assistant
- publishable on HACS

## Architecture (decided)

- **Server** (`server/`, Python ≥3.11, FastAPI):
  - Holds one Laya checkpoint (`english` | `multilingual`), resident and warmed up.
  - The pipeline turns text into ordered intent calls with IDs: fold → mentions → "if"
    clause → split → lexical guards → Laya intent question → targets (exact names →
    previous turn → single device → satellite area → Laya target question) → confidence
    gate → slots.
  - Conditions: the "if/wenn …" clause is resolved to one sensor (exact name, area, or the
    Laya target question) and tested **in code** against `ProcessRequest.states`. It
    guards the commands after it (or before it, when it comes last).
  - Follow-ups: the last command per `context_id` (hashed satellite device or conversation
    id), for `memory_seconds` (default 60). A clause without a verb reuses the previous
    intent; a command that names no device reuses the previous targets.
  - Live log: in-memory ring buffer, server-sent events (SSE) over an authenticated
    `fetch`, and a static UI.
- **Integration** (`custom_components/assist_decider/`, no pip requirements):
  - Pushes a names-only snapshot of exposed entities to the server.
  - Validates the returned actions and executes them in order via `intent.async_handle`
    with `assistant="conversation"`.
  - Speech comes from home-assistant-intents templates. There is an optional fallback agent.
- **Protocol**: `protocol.py` v2, byte-identical in both places, pydantic v2.
- **Provider seam**: `DecisionProvider.predict(state, questions, lang)` (choice questions)
  in `providers.py`. A future generative model would replace `pipeline.decide()` instead.

### Key findings (2026-10-04)

- Laya (PyPI `laya==0.3.26`) only chooses between options: no numbers, no splitting, no
  free text. It can't run inside HA OS, because torch has no musllinux wheels.
- **Laya is sensitive to the option set.** Removing options made the multilingual
  checkpoint worse. We always show the same intent options and *mask* the
  guard-excluded ones afterwards.
- **Option keys matter.** `turn_on`/`set_temperature` beat `HassTurnOn` and plain letters
  (12/16 vs 11/16 vs 9/16 on the German probe).
- Laya maps "is X on?" to `turn_on` with p≈1.0. Questions that name a device are therefore
  answered as state queries by rule; this is read-only and harmless.
- HA's built-in intent handlers return **empty speech**, so we render
  home-assistant-intents templates. Response keys differ per language: the HA side picks
  them (`_response_keys`).
- **Laya can't do per-device decisions (probe, 2026-10-04).** Giving every device its own
  state (utterance + device) with role, action, value-tree and condition questions scored
  0/32 cases on multilingual and 0/17 on english. Every device got the action of the
  whole sentence. Naming the device in a yes/no question scored 7/32: the right device
  often scores highest, but mixed on/off still fails. Choosing exact values had
  confidence ~0.01–0.03, and "is it cold?" over -5…30 °C gave flat probabilities. Laya
  only answers about the utterance as a whole, so splitting, numbers and comparisons
  stay in code.
- Don't construct `ToolResultContent` in the chat log (2026.9 → 2026.10 API break). We
  only add a final `AssistantContent`.

### Measurements (MacBook M4 Pro, MPS)

| Checkpoint | Load | Memory (MPS) | Decision p50 / max | Live test |
|---|---|---|---|---|
| english | 0.9–2.4 s | ~1.7 GB | 31 ms / 61 ms | 11/11 EN |
| multilingual | 2.4–2.9 s | ~1.3 GB | 18 ms / 31 ms | 11/11 DE |

Real HA 2026.9.4 end-to-end (dev instance, demo devices): 18/18 EN+DE commands executed
and spoken correctly (lights, brightness, thermostat, cover, queries, compounds,
fallback/error wording).

---

## Milestones

### M0: Server ✅ (session 1)

- [x] uv project, pinned `laya==0.3.26`, lockfile, CLI `assist-decider` (serve, gen-token, download)
- [x] Settings: defaults < TOML < `ASSIST_DECIDER_*` env < CLI; validation; token required (≥32 chars)
- [x] `protocol.py` v1 (strict requests, lenient responses, length and ID limits)
- [x] `LayaProvider`: one checkpoint, reviewed-revision pin, warm-up, device auto
- [x] Intent table + EN/DE language data (descriptions, domain words, verbs, guards)
- [x] Numbers EN/DE: digits, words (unicode-rbnf), decimal comma, `komma`/`point`, %, °, durations, halves/quarters
- [x] Pipeline: mentions with German compounds, verb-aware compound splitting, multi-target, lexical guards with masking, exact/single/satellite shortcuts, fuzzy target question (≤10 options), sensitive devices never guessed, confidence gate, partial results, traces
- [x] App security: bearer auth before body, IP lockout, body limit (declared and streamed), security headers/CSP, no docs, single-worker inference + 503, SSE log stream
- [x] Live log UI (vanilla JS, textContent only)
- [x] Tests: 89 fast (numbers, pipeline, app security, config, protocol sync) + 22 live Laya cases

### M1: HA integration ✅ (session 1)

- [x] manifest, hacs.json (min HA 2026.9.0: needs `er/dr.async_get_effective_area_id`), brand icon, config flow (URL/token/TLS), reauth, reconfigure, options (thresholds, fallback, no self-loop)
- [x] Setup: handshake, NotReady / AuthFailed / repair issue on protocol mismatch
- [x] Conversation entity: snapshot of exposed entities only, satellite area, action validation (intent allowlist, exposed IDs, slot keys), sequential execution, template speech EN/DE, HA-native error wording with friendly names, natural numbers ("21,5"), partial-result speech, fallback agent, chat log
- [x] Diagnostics (token redacted), strings + en/de translations
- [x] Tests: 35 with pytest-homeassistant-custom-component (HA 2026.9.4)
- [x] Real HA dev instance end-to-end (`hass -c .ha-config`, demo devices)

### M2: Language and coverage (next)

- [ ] **Yes/no state answers**: send a `state` slot ("an"/"on" → `on`) and use the `one_yesno` template. Currently German says "Bed light ist on".
- [ ] Mixed polarity in one clause without a second verb ("Licht an und Heizung aus"): split on particles and swap the intent per segment
- [ ] More intents: HassFanSetSpeed, HassSetVolume, media pause/unpause/next/previous, HassStartTimer/HassCancelTimer/HassTimerStatus (durations are already parsed), HassGetCurrentTime/Date, HassNevermind, HassStopMoving
- [ ] Brightness phrases (max/min/half), cover "halb"/"half" → 50
- [ ] HassGetState with area + domain + state ("are any lights on in the kitchen?") → `any`/`all` templates
- [ ] Floors as targets
- [x] Conditions ("if it is cold outside …", "wenn das Fenster offen ist …") with sensor values sent by HA, thresholds in options, `skipped` spoken
- [x] Follow-ups within `memory_seconds` ("turn it off", "and the hallway too", "23 degrees")
- [ ] Generative planner as an optional provider for compound and conditional commands that the lexical split gets wrong (idea from the probe: a 1.5–3B model with JSON-constrained output)
- [ ] Check each `_response_keys` choice against the intents JSON in a test, for every supported intent × language
- [ ] Log UI: per-segment collapse, copy-as-test-case button

### M3: HACS readiness

- [ ] **Re-run the review dimensions that were cut off:** pipeline correctness (adversarial EN/DE inputs) and HA integration vs HA source (hassfest/HACS validation, API usage)

- [ ] Create the GitHub repo `d00kIe/assist-decider` (description, topics: `home-assistant`, `hacs`, `assist`, `voice`, `laya`)
- [ ] Commit, push, confirm CI (ci.yaml, validate.yaml: hassfest + HACS action) is green, and fix findings
- [ ] Dark brand variants (`dark_icon.png`), `logo.png`
- [ ] Exception translations (`translation_key` on raised errors)
- [ ] First release v0.1.0 (release.yaml attaches `assist_decider.zip`)
- [ ] Later: PR to `hacs/default`

### M4: Accuracy and calibration

- [ ] Eval sets: 60+ EN and 60+ DE realistic commands, with a fixture home in `server/tests/eval/`
- [ ] `assist-decider eval` command: accuracy, escalation rate, confusions, p50/p95, threshold sweep
- [ ] Tune default thresholds per language, German option wording, option-order shuffle test
- [ ] Optional: per-language temperature fit (`lang_temperatures`), document fine-tuning

### M5: Production deployment

- [ ] Linux + NVIDIA: verify the install, document the torch CUDA index for older GPUs, measure VRAM and latency on the 4 GB GeForce (both checkpoints, alone and together)
- [ ] Windows + NVIDIA: verify the CUDA torch install command in the README
- [ ] Ship `deploy/`: systemd unit (`LoadCredential` for the token), launchd plist, Dockerfile (CUDA + CPU), compose file, Caddy TLS example
- [ ] CI: use the CPU torch index on Linux runners (smaller downloads)
- [ ] Publish the server to PyPI (trusted publishing), so `uv tool install assist-decider` works

### Later / ideas

- [ ] Wyoming "intent" mode (no custom integration needed; needs an HA token to read exposure)
- [ ] More providers: ONNX/CoreML Laya, NLI zero-shot (mDeBERTa), cross-encoder rerankers, GLiNER for free-text slots
- [ ] Choose or switch the server model from HA (admin endpoint)
- [ ] Hash-only context with resync (fewer bytes per request)
- [ ] State-aware disambiguation ("the light that is on")
- [ ] Light colors and kelvin; free-text intents (shopping list, broadcast, media search)
- [ ] Batch the Laya questions across segments
- [ ] Option to redact utterances from logs

---

## Session log

### Session 1 (2026-10-04)

- Research: Laya model and API, home-assistant-laya, HA 2026.9/2026.10 conversation and intent APIs, Wyoming as an alternative (rejected: no auth, no entity context), HACS rules.
- Built M0 + M1, tests green: server 89 fast + 22 live; integration 35.
- Adversarial review (4 reviewers + verifiers; two reviewers hit the session limit, so the pipeline and HA-integration dimensions were *not* reviewed). Fixed: slow-client connection exhaustion (header/body timeouts), lockout blocking the valid token, bounded fuzzy matching, NFKC length overflow, bounded log events, log-line forging, token vs token_file conflict, minimum HA version 2026.9, docs accuracy.
- Verified end to end against a real HA 2026.9.4 instance on the Mac.
- **Not done:** no git commit or push yet; nothing tested on Linux, Windows or CUDA; no Docker.

### How to resume

```bash
cd server && uv sync && uv run pytest && uv run pytest -m slow -s    # server
cd .. && uv sync --python 3.14 && uv run pytest tests                # integration
# Real HA dev instance (config in .ha-config/, git-ignored):
uv run hass -c .ha-config   # http://127.0.0.1:8124, integration symlinked in .ha-config/custom_components
```

### Session 2 (2026-10-04)

- Probed a device-centric decision tree on Laya (see Key findings): rejected.
- Protocol v2: `context_id`, `states` (condition values only), `Options.memory_seconds/cold_below/warm_above`, `ProcessResponse.skipped`.
- Server: "if" clause → one sensor → test in code; follow-up memory per context. Integration: sends hashed context and states, speaks skipped commands, three new options (EN/DE).
- Tests: server 99 fast (10 new) + live conditions and follow-ups, all green on both checkpoints; integration 36.
- **Not done:** real HA end-to-end check of conditions and follow-ups; not yet checked on a real satellite that consecutive wake-word turns share the device id the memory is keyed on.
