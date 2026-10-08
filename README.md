<p align="center"><img src="custom_components/assist_decider/brand/icon.png" width="96" alt=""></p>

# Assist Decider

**Fast, local, non-generative voice command understanding for Home Assistant.**
The model runs on a separate machine with a GPU, Apple Silicon or just a CPU, *not*
on your Home Assistant box.

Assist Decider is a [Home Assistant](https://www.home-assistant.io/) conversation agent.
It hands each spoken command to a small decision server, which works out *which devices*
you mean and *what to do* with each. Home Assistant then runs the result through its own
intent handlers. The server uses a small decision model such as
[Intern-Decision](https://huggingface.co/internlm/Intern-Decision-0.8B) or
[Laya](https://github.com/NandhaKishorM/laya). These models don't write text. They only
pick from a fixed list of answers, so they can't invent devices or actions.

```
"Mach das Küchenlicht an und stell die Heizung Bad auf 20 Grad"
   → HassTurnOn {name: light.kitchen}  → "Küchenlicht eingeschaltet"
   → HassClimateSetTemperature {name: climate.bathroom, temperature: 20}  → "Temperatur auf 20 Grad gestellt"
```

> **Status: early (v0.1).** It works end to end for the commands listed under
> [What it understands](#what-it-understands), in English and German. Tested on macOS
> (Apple Silicon) with Home Assistant 2026.9. Linux/CUDA and Windows instructions are
> still unverified. See [PLAN.md](PLAN.md) for the roadmap.

---

## Contents

- [How it works](#how-it-works)
- [What it understands](#what-it-understands)
- [Requirements](#requirements)
- [1. Install the decision server](#1-install-the-decision-server)
- [2. Install the Home Assistant integration](#2-install-the-home-assistant-integration)
- [3. Set up Assist](#3-set-up-assist)
- [Live log](#live-log)
- [Configuration reference](#configuration-reference)
- [Security](#security)
- [Privacy](#privacy)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Development](#development)
- [Credits and license](#credits-and-license)

## How it works

```
 Voice satellite ──(speech-to-text, e.g. Whisper)──► Home Assistant Assist pipeline
                                                        │  agent: Assist Decider
                                                        ▼
 ┌─ Home Assistant: custom_components/assist_decider ─────────────────────────────┐
 │ 1. Collect what is exposed to Assist: entity IDs, names, aliases, device class, │
 │    area and floor names. No states.                                             │
 │ 2. POST /v1/process (bearer token) ─────────────────────────────────────────┐   │
 │ 5. Check every proposed action: allowed intent, exposed device, allowed slots│   │
 │ 6. Run the actions in order through HA's own intent handlers                 │   │
 │ 7. Speak the reply using HA's built-in response templates (EN/DE)            │   │
 │    or hand the request to a fallback agent you chose                         │   │
 └──────────────────────────────────────────────────────────────────────────────┼───┘
                                                                                ▼
 ┌─ assist-decider server (your Mac / Linux box / Windows PC) ───────────────────────┐
 │ 3. Find the devices by name: device and room names and aliases you set in HA.     │
 │    Nothing named: the room of the satellite you spoke to.                         │
 │ 4. Ask the model short multiple-choice questions: command or question? which      │
 │    device in that room? what to do with each device? which number?                │
 │    Unsure about any answer: hand the whole sentence back to Home Assistant.       │
 └───────────────────────────────────────────────────────────────────────────────────┘
```

The server's steps are explained in [HOW-IT-WORKS.md](HOW-IT-WORKS.md). The ideas behind them:

- **Code finds the devices, the model decides what to do.** Device and room names are
  matched exactly against your Home Assistant names and aliases. The model then answers
  a few multiple-choice questions, and only ever sees options that make sense: a lock is
  offered "lock / unlock", never "set brightness".
- **What you said always wins.** When you say "off" or "aus", the model can't choose
  "turn on". When you say "on" and "off" in one sentence, each device follows the word
  nearest to its name.
- **Unsure means hands off.** If any answer is below your confidence threshold, nothing
  runs and the sentence goes to your fallback agent.
- **The server holds no Home Assistant credentials.** It can only *propose* actions;
  Home Assistant decides what actually runs.
- **Safety by construction.** Locks and garage, gate and door covers are only used when you
  say their exact name. A room command never picks them.

## What it understands

| What | English | Deutsch |
|---|---|---|
| Turn on / off (lights, switches, fans, media players, scenes, scripts, …) | "turn off the kitchen light" | "Schalte das Küchenlicht aus" |
| Open / close (blinds, valves, garage door), lock / unlock | "close the living room blinds", "lock the front door" | "Schließ das Garagentor", "Sperr die Haustür ab" |
| Brightness | "set the desk lamp to 30%" | "Stell das Küchenlicht auf fünfzig Prozent" |
| Thermostat | "set the bathroom heating to 21.5 degrees" | "Stell die Heizung Bad auf 21,5 Grad" |
| Blind position | "open the living room blinds to 40%" | "Fahre den Rollladen Wohnzimmer auf 40 Prozent" |
| Questions | "is the front door locked?", "what's the temperature in the bathroom?" | "Ist die Haustür abgeschlossen?", "Wie warm ist es im Bad?" |
| **Several devices, each its own action** | "turn on the kitchen light and turn off the hallway light" | "Mach das Küchenlicht an und den Fernseher aus" |
| **A room instead of a device** | "turn off the light in the hallway" | "Mach das Licht im Flur aus" |
| **Nothing named**: the satellite's room | "turn on the light" (said in the kitchen) | "Licht aus" (im Schlafzimmer) |
| **Follow-ups** (within a minute) | "turn it off" | "Mach es aus" |

Devices are named by their name or any alias you set in Home Assistant. Rooms are named
by area name or alias, and German compounds work ("Wohnzimmerlicht"). When you name a room,
the model picks the one device in it that you mean. When a number is said, only devices
that can take it are offered: "22 degrees" means the thermostat, not the lamp. Anything
else, such as "what's the capital of France", goes to your fallback agent if you
configured one. Otherwise you hear a polite "Sorry, I couldn't understand that".

## Requirements

**Decision server** (any one machine on your network):

| Platform | Status | Notes |
|---|---|---|
| macOS 14+ on Apple Silicon | ✅ tested (M4 Pro) | Uses the GPU (Metal/MPS). Intel Macs are not supported by PyTorch. |
| Linux x86_64 / aarch64 with NVIDIA GPU | ⚠️ expected to work, not yet verified | 1.2–2.3 GB VRAM for the smaller models. |
| Linux / Windows CPU only | ⚠️ expected to work, slower | Several seconds per sentence with the bigger models. |
| Windows with NVIDIA GPU | ⚠️ not yet verified | Needs the CUDA build of PyTorch, see below. |

- Python 3.11+ (installed for you by `uv`)
- 1–9 GB of disk for the model download, depending on the model
- RAM or VRAM while running: **1.2 GB** (`multilingual`) to **8.8 GB** (`h2o-lightning-4b`),
  see [Which model?](#which-model)

**Home Assistant**: 2026.9 or newer. No extra Python packages are installed into Home
Assistant, so it works on Home Assistant OS, Container and Core alike.

## 1. Install the decision server

> **Before the first release** the GitHub repository is not public yet. Until then,
> replace `"git+https://github.com/d00kIe/assist-decider#subdirectory=server"` with the
> path of your checkout's server folder: `uv tool install ./server`.

### macOS (Apple Silicon) and Linux

```bash
# 1. Install uv (Python package manager): https://docs.astral.sh/uv/
curl -LsSf https://astral.sh/uv/install.sh | sh         # or: brew install uv

# 2. Install the server as a command-line tool
uv tool install "git+https://github.com/d00kIe/assist-decider#subdirectory=server"

# 3. Create a token (a shared secret for Home Assistant) and keep it somewhere safe
assist-decider gen-token

# 4. Download the model once (about 0.7–0.9 GB)
assist-decider download --model multilingual

# 5. Run it. 0.0.0.0 lets Home Assistant on another machine connect.
ASSIST_DECIDER_TOKEN='<your token>' assist-decider --host 0.0.0.0 --model multilingual
```

You should see:

```
INFO assist_decider_server.providers: Loaded Laya multilingual (...) on mps in 2.4s
INFO assist_decider_server.app: Ready: provider=laya model=multilingual device=mps languages=en,de
INFO uvicorn.error: Uvicorn running on http://0.0.0.0:8765
```

On macOS, allow incoming connections when the firewall asks.

### Which model?

The server keeps exactly **one** model in memory. Each needs its own confidence threshold
(set in Home Assistant, see [Options](#add-it)). Results are from
[BENCHMARK.md](BENCHMARK.md): 55 test sentences, English and German, on an M4 Pro Mac.

| `--model` | Languages | Memory | Time per sentence | Threshold | Right · handed off · wrong |
|---|---|---|---|---|---|
| `multilingual` (Laya, default) | English, German | 1.2 GB | 40 ms | 0.4 | 45 · 5 · 5 |
| `english` (Laya) | English | 2.0 GB | 60 ms | 0.2 | 31 · 3 · 0 (of 34) |
| `kev-0.8b` | English, German* | 2.3 GB | 0.2 s | 0.4 | 39 · 15 · 1 |
| **`intern-decision-0.8b`** | English, German | 2.3 GB | 0.5 s | **0.2** | **47 · 8 · 0** |
| `intern-decision-2b` | English, German | 4.6 GB | 0.7 s | 0.2 | 52 · 3 · 0 |
| `h2o-lightning-4b` | English, German* | 8.8 GB | 1.1 s | 0.1 | 53 · 2 · 0 |

\* trained mostly on English; German works in the test but isn't promised by its makers.

**Recommended: `intern-decision-0.8b` with a threshold of 0.2.** It makes no mistakes in
the test and fits a 4 GB graphics card. On an NVIDIA card it should be much faster than on
the Mac. Laya (`multilingual`) is the fastest but makes the most mistakes. Use
`h2o-lightning-4b` or `intern-decision-2b` when you have the memory.

Want two languages with different models, for example an English "Jarvis" and a German
"Nabu" assistant? Run two servers on different ports (`--port 8765 --model english` and
`--port 8766 --model multilingual`) and add each one in Home Assistant. You get one
conversation agent per server.

### Windows

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv tool install "git+https://github.com/d00kIe/assist-decider#subdirectory=server"
assist-decider gen-token
assist-decider download --model multilingual
$env:ASSIST_DECIDER_TOKEN = "<your token>"
assist-decider --host 0.0.0.0 --model multilingual
```

The PyTorch package on PyPI is CPU-only on Windows. For an NVIDIA GPU, add the CUDA
build (not yet verified, see [PLAN.md](PLAN.md)):
`uv tool install --reinstall "git+https://github.com/d00kIe/assist-decider#subdirectory=server" --with torch --index https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match`

### Keep it running

Run it like any long-running service: a `launchd` agent on macOS, a `systemd` service on
Linux, or Task Scheduler on Windows. Pass the token through an environment variable or a
`token_file` readable only by the service user. Ready-made unit files are on the roadmap
(PLAN.md, milestone 5). A minimal systemd unit:

```ini
# /etc/systemd/system/assist-decider.service
[Service]
User=assist
Environment=ASSIST_DECIDER_TOKEN_FILE=/etc/assist-decider/token
ExecStart=/home/assist/.local/bin/assist-decider --host 0.0.0.0 --model multilingual
Restart=on-failure
[Install]
WantedBy=multi-user.target
```

## 2. Install the Home Assistant integration

### Via HACS (recommended)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/d00kIe/assist-decider`,
   type **Integration**.
2. Search for **Assist Decider**, download it, and restart Home Assistant.

### Manually

Copy `custom_components/assist_decider` into your Home Assistant `config/custom_components/`
folder (for example via the Samba or File editor add-on) and restart Home Assistant.

### Add it

**Settings → Devices & services → Add integration → Assist Decider**, then enter:

- **Server URL**: `http://<server-ip>:8765`
- **Token**: the one from `assist-decider gen-token`
- **Verify TLS certificate**: leave on, it only matters for `https://`

The integration checks the connection and token right away. The new agent appears as
`conversation.assist_decider_<model>`.

**Options** (⚙ on the integration):

| Option | Default | Meaning |
|---|---|---|
| Confidence threshold (English) | 0.40 | If the model is less sure than this about any answer, nothing runs and the sentence goes to the fallback agent. Higher is safer, lower understands more. Set it to the value for your model in [Which model?](#which-model). |
| Confidence threshold (German) | 0.50 | The same for German. |
| Follow-up memory (seconds) | 60 | How long "turn it off" refers to the devices of the previous command, per satellite (or conversation). 0 turns it off. |
| Fallback agent | none | Where requests go that Assist Decider can't decide, e.g. *Home Assistant* or an LLM agent. |

Only entities **exposed to Assist** are ever sent or controlled
(Settings → Voice assistants → Expose). Give devices aliases in their entity settings to
add alternative names, including in another language ("Küchenlicht").

## 3. Set up Assist

**Settings → Voice assistants → Add assistant**:

- **Conversation agent**: *Assist Decider (…)*
- **Language**: English or German (the list shows what your server's model supports)
- Speech-to-text and text-to-speech: whatever you use (e.g. Whisper and Piper)

One assistant per language works well.

**Tip:** turn on **"Prefer handling commands locally"** in the assistant. Home Assistant's
built-in sentence matcher then answers exact matches instantly, and only everything else
reaches Assist Decider.

Try it in the Assist dialog (the chat icon) before using voice.

## Live log

Open `http://<server-ip>:8765/` in a browser and paste the token. It is kept in that
browser tab only. You see every decision as it happens:

- the sentence, language, satellite room and timing
- how the devices were found, and the numbers that were said
- every model question with the probability of each option, which options were ruled
  out, the confidence and the threshold
- the final intent calls, or why the sentence was handed back, plus a filterable server log

The **Home** tab shows every exposed device and room, the words that match it, and what
the model sees and can do with it.

## Configuration reference

The server reads, in increasing priority: built-in defaults → a TOML file (`--config` or
`ASSIST_DECIDER_CONFIG`) → environment variables `ASSIST_DECIDER_<NAME>` → command-line
flags. See [`server/assist-decider.example.toml`](server/assist-decider.example.toml).

| Setting | CLI | Default | Description |
|---|---|---|---|
| `host` | `--host` | `127.0.0.1` | Listen address. `0.0.0.0` for the LAN. |
| `port` | `--port` | `8765` | |
| `token` / `token_file` | – | – | **Required**, at least 32 characters. |
| `model` | `--model` | `multilingual` | see [Which model?](#which-model) |
| `device` | `--device` | `auto` | `auto`, `cpu`, `cuda`, `cuda:N`, `mps`, `xpu` |
| `max_pending` | – | `4` | Queued requests before HTTP 503 |
| `max_body_bytes` | – | `1048576` | Largest request accepted |
| `log_buffer` | – | `2000` | Events kept for the live log |
| `log_level` | `--log-level` | `INFO` | `DEBUG` also logs the full exposed-device list |
| `tls_certfile` / `tls_keyfile` | – | – | Serve HTTPS directly |

Commands: `assist-decider` (serve), `assist-decider gen-token`,
`assist-decider download [--model …]`.

## Security

- **A token is always required.** The server refuses to start without one (32+
  characters). Comparison is constant-time, and authentication happens *before* a request
  body is read. After 10 failed attempts an address gets HTTP 429 for 15 minutes. A
  request with the *correct* token always gets through, so nobody can lock Home Assistant
  out, even when every client shares one address behind a reverse proxy.
- **Slow or silent clients are dropped:** a connection must send its request headers
  within 5 s and its body within 10 s.
- **Listens on localhost by default.** Exposing it to the LAN is an explicit `--host` choice.
- **Hardened HTTP:** request size limit, strict input validation (unknown fields rejected,
  IDs checked against patterns), one inference at a time with a bounded queue, no CORS,
  no API docs endpoint, and a strict Content-Security-Policy on the web UI. The UI never
  renders text as HTML.
- **Least privilege:** the server holds no Home Assistant credentials. Home Assistant
  re-checks every action it receives (allowed intent, exposed device, allowed slots) and
  executes with Assist's exposure rules.
- **Supply chain:** the model is downloaded from a pinned, upstream-reviewed commit
  (safetensors only, no remote code). `laya` is pinned exactly. Other dependencies are
  locked (`uv.lock`) for development and CI. `uv tool install` resolves them fresh within
  the declared ranges.
- **Plain HTTP on your LAN means the token travels unencrypted.** On untrusted networks,
  enable TLS (`tls_certfile`/`tls_keyfile`), put a reverse proxy such as Caddy in front,
  or use a VPN such as Tailscale. Rotate the token by generating a new one and entering it
  in Home Assistant, which will ask.

Found a vulnerability? Please open a private security advisory on GitHub instead of a
public issue.

## Privacy

Everything stays on your network. The server receives what you said, the names, aliases
and device classes of *exposed* entities, and your area and floor names. Device
**states** are not sent. Utterances appear in the live view (memory only, gone after a
restart) and in the server log on stderr. Whatever captures stderr (journald, a launchd
log file, Docker) may keep them on disk. Use `--log-level WARNING` to keep utterances out
of the log.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Cannot reach the server" when adding the integration | Is the server running with `--host 0.0.0.0`? Can Home Assistant reach the port (macOS firewall, Windows Defender, `curl http://<ip>:8765/healthz`)? |
| "The server rejected the token" | Paste the exact token, without quotes or spaces. |
| Assistant says "the decision server is not reachable" | The server is down or restarting. Home Assistant retries setup automatically. |
| A command is not executed | Open the live log. Usually it is `low_confidence` (check the threshold for your model in [Which model?](#which-model)) or `no_target` (no device or room name was recognized: add an alias to the device, or give the satellite a room). |
| The wrong device in a room is used | Name the device instead of the room, or give it an alias that you say. |
| German is less reliable than English | Use `intern-decision-0.8b` or bigger, add German aliases, and configure a fallback agent. |
| Every command is slow | The model warms up at startup. If it is still slow, check that the log says `device=mps`/`cuda`, not `cpu`. |

## Limitations

- **A room means one device.** "Turn off the lights in the living room" turns off the one
  light the model picks, not all of them. Name each device, or use a Home Assistant
  group or area automation.
- The models only *choose*, so free text such as shopping list items, broadcast messages
  and media search is not supported.
- Not yet supported: light colors and color temperature, timers, media controls, fan speed,
  volume and floors (see the roadmap).
- No follow-up questions ("which light?"). Follow-ups only reuse the previous devices
  ("turn *it* off"), not the previous action ("and the kitchen too").
- Conditions ("if it is cold outside …", "wenn …") are not supported. Such sentences go
  to the fallback agent; use a Home Assistant automation instead.
- A thermostat question is answered with its temperature, also "is the heating on?".

## Development

```bash
# Server: fast tests, then live tests with real models
cd server && uv sync && uv run pytest && uv run pytest -m slow -s

# The benchmark of BENCHMARK.md (55 sentences, any models)
cd server && uv run python tests/eval/benchmark.py multilingual intern-decision-0.8b

# Home Assistant integration tests (Home Assistant 2026.9.4, Python 3.14)
uv sync --python 3.14 && uv run pytest tests
```

- The wire protocol lives in `protocol.py`, which must be **byte-identical** in
  `server/assist_decider_server/` and `custom_components/assist_decider/`. Tests and CI
  check this.
- New model? Implement `DecisionProvider` (`server/assist_decider_server/providers.py`).
  It answers multiple-choice questions, and the whole pipeline is reused. Run the
  benchmark to find its threshold.
- New language? Add a `Lang` entry in `lang.py` (number words come from unicode-rbnf).
  Then extend `language` in `protocol.py` (both copies) and the per-language thresholds
  and messages in the integration's `const.py`, `strings.json` and translations.

Project status and next steps: [PLAN.md](PLAN.md).
Which decision model to use, with accuracy, memory and speed: [BENCHMARK.md](BENCHMARK.md).

## Credits and license

Apache License 2.0. See [LICENSE](LICENSE).

- The decision models, all Apache-2.0: [Laya](https://github.com/NandhaKishorM/laya) by Convai Innovations, [Intern-Decision](https://huggingface.co/internlm/Intern-Decision-0.8B) by InternLM, [Kev](https://huggingface.co/jaredpalmer/kev-0.8b) by Jared Palmer, [H2O-Lightning](https://huggingface.co/h2oai/h2o-lightning-4b) by H2O.ai
- [home-assistant-laya](https://github.com/allenporter/home-assistant-laya) by Allen Porter: the original in-process integration that inspired this project
- [Home Assistant](https://github.com/home-assistant/core) (Apache-2.0): response rendering follows the built-in conversation agent; replies use the [intents](https://github.com/OHF-Voice/intents) response templates
- [unicode-rbnf](https://github.com/rhasspy/unicode-rbnf) for spoken numbers
