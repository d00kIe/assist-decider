<p align="center"><img src="custom_components/assist_decider/brand/icon.png" width="96" alt=""></p>

# Assist Decider

**Fast, local, non-generative voice command understanding for Home Assistant.**
The model runs on a separate machine with a GPU, Apple Silicon or just a CPU, *not*
on your Home Assistant box.

Assist Decider is a [Home Assistant](https://www.home-assistant.io/) conversation agent.
It hands each spoken command to a small decision server, which works out *what* to do and
*which device* to do it to. Home Assistant then runs the result through its own intent
handlers. It is built on [Laya](https://github.com/NandhaKishorM/laya), a
non-autoregressive decision model. Laya does not generate text; it scores a fixed set of
options in a single forward pass, so it cannot invent devices or actions. A typical
decision takes **15–40 ms** on an Apple M-series GPU.

```
"Mach das Küchenlicht an und stell das Schlafzimmer auf 20 Grad"
   → HassTurnOn {name: light.kitchen}  → "Kitchen Lights eingeschaltet"
   → HassClimateSetTemperature {area: bedroom, temperature: 20}  → "Temperatur auf 20 Grad gestellt"
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
 │    area and floor names, plus sensor/weather/thermostat values for conditions.  │
 │ 2. POST /v1/process (bearer token) ─────────────────────────────────────────┐   │
 │ 5. Check every proposed action: allowed intent, exposed device, allowed slots│   │
 │ 6. Run the actions in order through HA's own intent handlers                 │   │
 │ 7. Speak the reply using HA's built-in response templates (EN/DE)            │   │
 │    or hand the request to a fallback agent you chose                         │   │
 └──────────────────────────────────────────────────────────────────────────────┼───┘
                                                                                ▼
 ┌─ assist-decider server (your Mac / Linux box / Windows PC) ───────────────────────┐
 │ 3. Fold text, find spoken device and room names, split "…and…" into commands,     │
 │    read numbers ("einundzwanzig komma fünf Grad" → 21.5)                          │
 │ 4. Ask Laya: which action? which device or room? Gate on confidence.              │
 │    Model stays loaded in RAM/VRAM. Live web UI shows every decision.              │
 └───────────────────────────────────────────────────────────────────────────────────┘
```

Responsibilities are split deliberately:

- **The server holds no Home Assistant credentials.** It can only *propose* actions;
  Home Assistant decides what actually runs.
- **The model is the core but does not work alone.** Laya picks *between options*.
  Numbers, compound commands and exact device names are handled by fast, deterministic
  code around it. Lexical guards stop the model from contradicting what you said: when
  you say "aus", "turn on" is never chosen. Every guard is visible in the live log.
- **Safety by construction.** Locks and garage/gate/door covers are never *guessed*.
  They only act when you say their exact name. A room command never includes locks.

## What it understands

| Intent | English | Deutsch |
|---|---|---|
| Turn on / off (lights, switches, fans, media players, covers, scenes, scripts, …) | "turn off the kitchen light", "turn on the lights in the hallway" | "Schalte das Küchenlicht aus", "Mach das Licht im Flur an" |
| Brightness | "set the bed light to 30%" | "Stell das Bettlicht auf fünfzig Prozent" |
| Thermostat | "set the bedroom to 21.5 degrees" | "Stell die Heizung im Bad auf 21,5 Grad" |
| Cover position | "open the living room blinds to 40%" | "Fahre den Rollladen Wohnzimmer auf 40 Prozent" |
| Device state | "is the front door locked?" | "Ist die Haustür abgeschlossen?" |
| Temperature | "how warm is it in the living room?" | "Wie warm ist es im Wohnzimmer?" |
| **Compound commands** | "turn off the kitchen light and set the bedroom to 19 degrees" | "Mach die Kaffeemaschine an und stell das Bad auf 22 Grad" |
| **Several targets** | "turn off the lights in the kitchen and the hallway" | "Schalte das Licht in der Küche und im Flur aus" |
| **Conditions** | "if it is cold outside set the heating to 24 and open the blinds" | "Wenn es draußen kalt ist, stell die Heizung im Bad auf 22 Grad" |
| **Follow-ups** (within a minute) | "turn it off", "and the hallway too", "23 degrees" | "Mach es aus", "und im Flur auch" |

Devices can be named by their name or any alias you set in Home Assistant. Rooms are named
by area name or alias. German compounds work ("Wohnzimmerlicht"). Without a room, the
satellite's own area is used ("turn on the lights" in the kitchen). Anything else, such as
"what's the capital of France", is passed to your fallback agent if you configured one.
Otherwise you hear a polite "Sorry, I couldn't understand that".

## Requirements

**Decision server** (any one machine on your network):

| Platform | Status | Notes |
|---|---|---|
| macOS 14+ on Apple Silicon | ✅ tested (M4 Pro) | Uses the GPU (Metal/MPS). Intel Macs are not supported by PyTorch. |
| Linux x86_64 / aarch64 with NVIDIA GPU | ⚠️ expected to work, not yet verified | ~1.3–1.7 GB VRAM. |
| Linux / Windows CPU only | ⚠️ expected to work, slower | Roughly 0.2–0.6 s per decision. |
| Windows with NVIDIA GPU | ⚠️ not yet verified | Needs the CUDA build of PyTorch, see below. |

- Python 3.11+ (installed for you by `uv`)
- About 2 GB of disk for the model download (one checkpoint)
- RAM or VRAM: about **1.3 GB** (`multilingual`) or **1.7 GB** (`english`) while running

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

**Which model?** The server keeps exactly **one** checkpoint in memory:

| `--model` | Languages | Memory | Notes |
|---|---|---|---|
| `english` | English | ~1.7 GB | Most accurate for English |
| `multilingual` | English, German | ~1.3 GB | Default. German accuracy is lower than English. |

Want the best of both, for example an English "Jarvis" and a German "Nabu" assistant?
Run two servers on different ports (`--port 8765 --model english` and
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
| Confidence threshold (English) | 0.40 | Below this, a command is not executed. Higher is safer, lower understands more. |
| Confidence threshold (German) | 0.50 | Stricter because German is less accurate. |
| Follow-up memory (seconds) | 60 | How long "turn it off" refers to the previous command, per satellite (or conversation). 0 turns it off. |
| "Cold" below / "Warm" above | 12 / 20 | Thresholds for "if it is cold/warm …", in your temperature unit. "below 5 degrees" uses the spoken number. |
| Fallback agent | none | Where requests go that Assist Decider can't decide, e.g. *Home Assistant* or an LLM agent. If only part of a sentence is understood, the whole sentence goes to the fallback. |

Only entities **exposed to Assist** are ever sent or controlled
(Settings → Voice assistants → Expose). Give devices aliases in their entity settings to
add alternative names, including in another language ("Küchenlicht").

## 3. Set up Assist

**Settings → Voice assistants → Add assistant**:

- **Conversation agent**: *Assist Decider (…)*
- **Language**: English or German (the list shows what your server's model supports)
- Speech-to-text and text-to-speech: whatever you use (e.g. Whisper and Piper)

One assistant per language works well, for example "Jarvis" (English, `english` server)
and "Nabu" (German, `multilingual` server).

**Tip:** turn on **"Prefer handling commands locally"** in the assistant. Home Assistant's
built-in sentence matcher then answers exact matches instantly, and only everything else
reaches Assist Decider.

Try it in the Assist dialog (the chat icon) before using voice.

## Live log

Open `http://<server-ip>:8765/` in a browser and paste the token. It is kept in that
browser tab only. You see every decision as it happens:

- the utterance, language, satellite room and timing
- each detected command, extracted numbers, guards that removed options
- every model question with the probability of each option, the confidence and the
  threshold
- the final intent calls, plus a filterable server log

## Configuration reference

The server reads, in increasing priority: built-in defaults → a TOML file (`--config` or
`ASSIST_DECIDER_CONFIG`) → environment variables `ASSIST_DECIDER_<NAME>` → command-line
flags. See [`server/assist-decider.example.toml`](server/assist-decider.example.toml).

| Setting | CLI | Default | Description |
|---|---|---|---|
| `host` | `--host` | `127.0.0.1` | Listen address. `0.0.0.0` for the LAN. |
| `port` | `--port` | `8765` | |
| `token` / `token_file` | – | – | **Required**, at least 32 characters. |
| `model` | `--model` | `multilingual` | `english` or `multilingual` |
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
**states** are not sent, except the current values conditions can test: exposed sensors and
binary sensors, weather temperature and thermostat current temperature. Utterances appear in the live view (memory only, gone after a
restart) and in the server log on stderr. Whatever captures stderr (journald, a launchd
log file, Docker) may keep them on disk. Use `--log-level WARNING` to keep utterances out
of the log.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Cannot reach the server" when adding the integration | Is the server running with `--host 0.0.0.0`? Can Home Assistant reach the port (macOS firewall, Windows Defender, `curl http://<ip>:8765/healthz`)? |
| "The server rejected the token" | Paste the exact token, without quotes or spaces. |
| Assistant says "the decision server is not reachable" | The server is down or restarting. Home Assistant retries setup automatically. |
| A command is not executed | Open the live log. Usually it is `low_confidence` (lower the threshold slightly), `no_target` (add an alias to the device) or `area_without_domain` (say "lights": "turn off the kitchen **lights**"). |
| German is less reliable than English | Expected with the current Laya checkpoint. Add German aliases, keep the German threshold at 0.5, and configure a fallback agent. |
| The first command after start is slow | The model warms up at startup. If it is still slow, check that the log says `device=mps`/`cuda`, not `cpu`. |

## Limitations

- Laya only *chooses*, so free text such as shopping list items, broadcast messages and
  media search is not supported yet.
- Not yet supported: light colors and color temperature, timers, media controls, fans,
  volume and floors (see the roadmap).
- No follow-up questions ("which light?"). References to the previous command work for
  about a minute ("turn *it* off", "and the kitchen too").
- Conditions test one sensor (temperature: cold/warm/below/above; binary sensors:
  open/closed, on/off). Nested conditions ("if … and …") are not supported.
- Mixed on/off in one sentence without a second verb ("Licht an und Heizung aus") is
  ambiguous. Say "Mach das Licht an und schalte die Heizung aus".

## Development

```bash
# Server: fast tests, then live tests with the real model
cd server && uv sync && uv run pytest && uv run pytest -m slow -s

# Home Assistant integration tests (Home Assistant 2026.9.4, Python 3.14)
uv sync --python 3.14 && uv run pytest tests
```

- The wire protocol lives in `protocol.py`, which must be **byte-identical** in
  `server/assist_decider_server/` and `custom_components/assist_decider/`. Tests and CI
  check this.
- New model? Implement `DecisionProvider` (`server/assist_decider_server/providers.py`).
  It answers choice questions, and the whole pipeline is reused.
- New language? Add a `Lang` entry in `lang.py` (number words come from unicode-rbnf).
  Then extend `language` in `protocol.py` (both copies) and the per-language thresholds
  and messages in the integration's `const.py`, `strings.json` and translations.

Project status and next steps: [PLAN.md](PLAN.md).

## Credits and license

Apache License 2.0. See [LICENSE](LICENSE).

- [Laya](https://github.com/NandhaKishorM/laya) by Convai Innovations (Apache-2.0): the decision model
- [home-assistant-laya](https://github.com/allenporter/home-assistant-laya) by Allen Porter: the original in-process integration that inspired this project
- [Home Assistant](https://github.com/home-assistant/core) (Apache-2.0): response rendering follows the built-in conversation agent; replies use the [intents](https://github.com/OHF-Voice/intents) response templates
- [unicode-rbnf](https://github.com/rhasspy/unicode-rbnf) for spoken numbers
