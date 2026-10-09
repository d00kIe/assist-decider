<p align="center"><img src="custom_components/assist_decider/brand/icon.png" width="96" alt=""></p>

# Assist Decider

**Local voice commands for Home Assistant, understood by a small model on another machine.**

Assist Decider is a conversation agent for Home Assistant Assist. It sends what you said to a small
*decision server* on your network, for example a Mac, a PC with a graphics card, or any Linux box.
The server works out which devices you mean and what to do with each. Home Assistant then checks
the result and runs it with its own intent handlers.

The models only *choose* from fixed lists of devices and actions. They can't write text, so they
can't invent a device or an action.

```
"Mach das Küchenlicht an und stell die Heizung Bad auf 20 Grad"
   → turn on light.kitchen                     "Küchenlicht eingeschaltet"
   → set climate.bathroom to 20 °C             "Temperatur auf 20 Grad gestellt"
```

> **Status: early (v0.1).** English and German. Tested on macOS (Apple Silicon) with
> Home Assistant 2026.9. Linux, Windows and NVIDIA are expected to work but are not verified yet.

## What you can say

| | English | Deutsch |
|---|---|---|
| On / off: lights, switches, fans, media players, scenes, scripts | "turn off the kitchen light" | "Schalte das Küchenlicht aus" |
| Open / close / lock | "close the living room blinds", "lock the front door" | "Schließ das Garagentor" |
| Brightness, temperature, blind position | "set the desk lamp to 30%", "set the bathroom heating to 21.5 degrees" | "Stell die Heizung Bad auf 21,5 Grad" |
| Questions | "is the front door locked?", "what's the temperature in the bathroom?" | "Wie warm ist es im Bad?" |
| Several devices at once | "turn on the kitchen light and turn off the hallway light" | "Mach das Küchenlicht an und den Fernseher aus" |
| A room instead of a device | "turn off the light in the hallway" | "Mach das Licht im Flur aus" |
| Nothing named: the satellite's room | "turn on the light" | "Licht aus" |
| Follow-ups, within a minute | "turn it off" | "Mach es aus" |

Devices and rooms are recognized by the names and aliases you gave them in Home Assistant. If
the model is unsure, nothing happens and the sentence goes to a fallback agent, for example Home
Assistant's own agent or an LLM. See [Limitations](#limitations) for what it can't do yet.

## Install

You need two parts:

1. The **decision server**, on a machine with a GPU, Apple Silicon or a fast CPU. It does
   *not* run on your Home Assistant box.
2. The **integration** in Home Assistant 2026.9 or newer. It works on HA OS, Container and Core,
   and installs no extra Python packages.

### 1. Install the decision server

#### With Docker

*Coming soon.*

<!-- Docker image, docker run / compose example and GPU notes go here. -->

#### With uv (macOS, Linux, Windows)

[uv](https://docs.astral.sh/uv/) installs Python and the server for you.

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

uv tool install "git+https://github.com/d00kIe/assist-decider#subdirectory=server"
assist-decider download --model intern-decision-0.8b    # once, 1.6 GB (see "Choose a model")
assist-decider --host 0.0.0.0 --model intern-decision-0.8b
```

`--host 0.0.0.0` lets Home Assistant on another machine connect. On macOS, allow incoming
connections when the firewall asks. When the server is ready, it logs
`Ready: provider=… device=mps` (or `cuda`/`cpu`).

On **Windows with an NVIDIA card**, PyTorch from PyPI is CPU-only. Install the CUDA build with:
`uv tool install --reinstall "git+https://github.com/d00kIe/assist-decider#subdirectory=server" --with torch --index https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match`

To keep the server running, use a launchd agent (macOS), a systemd service (Linux) or Task
Scheduler (Windows). A minimal systemd unit:

```ini
# /etc/systemd/system/assist-decider.service
[Service]
User=assist
ExecStart=/home/assist/.local/bin/assist-decider --host 0.0.0.0 --model intern-decision-0.8b
Restart=on-failure
[Install]
WantedBy=multi-user.target
```

### 2. Choose a model

The server keeps one model in memory. Each model has its own confidence threshold, which you set
in Home Assistant (next step). The numbers come from [BENCHMARK.md](BENCHMARK.md): 65 test
sentences in English and German on an M4 Pro Mac.

| `--model` | Languages | Memory | Time per sentence | Threshold | Right · handed off · wrong |
|---|---|---|---|---|---|
| `multilingual` (Laya, the default) | English, German | 1.2 GB | 40 ms | 0.4 | 47 · 8 · 10 |
| `english` (Laya) | English | 2.0 GB | 70 ms | 0.2 | 32 · 4 · 3 (of 39) |
| `d1-omni-600m` (Liquid AI) | English, German | 1.0 GB | 50 ms | 0.4 | 42 · 19 · 4 |
| **`intern-decision-0.8b`** | English, German | 2.3 GB | 0.5 s | **0.2** | **48 · 14 · 3** |
| `intern-decision-2b` | English, German | 4.6 GB | 0.8 s | 0.2 | 54 · 6 · 5 |
| **`d1-3b`** (Liquid AI) | English, German | 6.8 GB | 0.3 s | **0.2** | **55 · 4 · 6** |

- **Up to 4 GB of graphics memory:** use `intern-decision-0.8b` with a threshold of 0.2.
- **8 GB or more, or an Apple Silicon Mac with 16 GB:** use `d1-3b` with a threshold of 0.2. It is
  the most accurate model, and on the original 55 test sentences it got none wrong.
- Most of the "wrong" sentences are "all the lights in …" commands. Every model gets those wrong
  today (see [Limitations](#limitations)).

Want an English and a German assistant with different models? Run two servers on different ports
(`--port 8765 --model english`, `--port 8766 --model multilingual`) and add each one in Home
Assistant.

### 3. Install the Home Assistant integration

- **HACS:** HACS → ⋮ → *Custom repositories* → add `https://github.com/d00kIe/assist-decider` as
  type *Integration*. Then download **Assist Decider** and restart Home Assistant.
- **Manually:** copy `custom_components/assist_decider` into your `config/custom_components/`
  folder and restart Home Assistant.

Then go to **Settings → Devices & services → Add integration → Assist Decider** and enter the
server URL, for example `http://192.168.1.20:8765`.

**Options** (the ⚙ on the integration):

| Option | Default | What it does |
|---|---|---|
| Confidence threshold (English / German) | 0.40 / 0.50 | Below this, nothing runs and the sentence goes to the fallback agent. Use the value for your model from the table above. |
| Follow-up memory (seconds) | 60 | How long "turn it off" refers to the previous command's devices. 0 turns this off. |
| Fallback agent | none | Gets every sentence Assist Decider can't decide, for example *Home Assistant* or an LLM agent. |

Only entities **exposed to Assist** are sent or controlled (Settings → Voice assistants →
Expose). Aliases add more names, also in another language ("Küchenlicht").

### 4. Use it

**Settings → Voice assistants → Add assistant**, then choose **Assist Decider** as the
conversation agent and pick English or German. One assistant per language works well.

- Try it first in the Assist dialog (the chat icon), then by voice.
- Turn on **"Prefer handling commands locally"**. Home Assistant then answers exact matches
  itself, instantly, and only the rest goes to Assist Decider.
- Open `http://<server-ip>:8765/` for the **live log**. It shows every sentence, the devices found,
  each model question with its probabilities, and what was run or why it was handed off. The
  *Home* tab shows what the server knows about your devices and rooms.

## Server settings

Settings are read from built-in defaults, then a TOML file (`--config`, see
[`assist-decider.example.toml`](server/assist-decider.example.toml)), then environment
variables `ASSIST_DECIDER_<NAME>`, then command-line flags. Later sources win.

| Setting | Flag | Default | |
|---|---|---|---|
| `host` | `--host` | `127.0.0.1` | `0.0.0.0` to accept connections from the LAN |
| `port` | `--port` | `8765` | |
| `model` | `--model` | `multilingual` | see [Choose a model](#2-choose-a-model) |
| `device` | `--device` | `auto` | `cpu`, `cuda`, `cuda:N`, `mps`, `xpu` |
| `log_level` | `--log-level` | `INFO` | `WARNING` keeps what you say out of the log |
| `tls_certfile` / `tls_keyfile` | | | serve HTTPS directly |
| `max_pending`, `max_body_bytes`, `log_buffer` | | `4`, `1 MiB`, `2000` | queue size, request size limit, live-log length |

## Security and privacy

- **There is no authentication.** Anyone who can reach the port can send requests and read the
  live log. Use it on a closed home network only, never on the internet. Firewall the port if
  your LAN has untrusted devices, and use TLS or a VPN if you want encryption.
- **The server holds no Home Assistant credentials.** It only *proposes* actions. Home Assistant
  checks each one (allowed intent, exposed device, allowed slots) before running it.
- **Locks and garage doors** are only used when you say their exact name. A room command never
  picks them.
- **Models are downloaded at a pinned commit**, as safetensors only. No remote code runs.
- **What is sent:** the sentence and the names, aliases and device classes of exposed entities,
  plus area and floor names. Device states are not sent. Sentences stay in memory for the live
  log, and appear in the server log unless `--log-level WARNING` is set.

Found a vulnerability? Please open a private security advisory on GitHub.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Cannot reach the server" when adding the integration | Did you start the server with `--host 0.0.0.0`? Check the firewall, and try `curl http://<ip>:8765/healthz` from another machine. |
| A command does nothing | Look in the live log. `low_confidence`: lower the threshold to the value for your model. `no_target`: no device or room name was recognized, so add an alias or give the satellite a room. |
| The wrong device in a room | Name the device, or give it an alias you actually say. |
| Everything is slow | Check that the log says `device=mps` or `cuda`, not `cpu`. |

## Limitations

- **A room means one device.** "Turn off the lights in the living room" switches the one light
  the model picks, not all of them. Floors ("upstairs") are not understood yet.
- Not supported yet: colors, timers, media controls, fan speed, volume, shopping lists.
- No follow-up questions ("which light?"). "If …" / "wenn …" sentences go to the fallback agent.

## Development

```bash
cd server && uv sync && uv run pytest                    # server tests (-m slow: real models)
cd server && uv run python tests/eval/benchmark.py intern-decision-0.8b d1-omni-600m
uv sync --python 3.14 && uv run pytest tests             # Home Assistant integration tests
```

[HOW-IT-WORKS.md](HOW-IT-WORKS.md) explains the pipeline step by step. [PLAN.md](PLAN.md) has the
roadmap. `protocol.py` must stay byte-identical in `server/` and `custom_components/`, and CI
checks this. To add a model, implement `DecisionProvider` in
`server/assist_decider_server/providers.py`, then run the benchmark to find its threshold.

## Credits and license

Apache License 2.0, see [LICENSE](LICENSE). The model weights are downloaded separately and keep
their own licenses:

- [Laya](https://github.com/NandhaKishorM/laya) by Convai Innovations and
  [Intern-Decision](https://huggingface.co/internlm/Intern-Decision-0.8B) by InternLM: Apache-2.0
- [d1-3B](https://huggingface.co/LiquidAI/d1-3B) and [d1-omni-600M](https://huggingface.co/LiquidAI/d1-omni-600M)
  by Liquid AI: LFM Open License v1.0, free unless your organization makes $10M or more a year
- [home-assistant-laya](https://github.com/allenporter/home-assistant-laya) by Allen Porter inspired
  this project. Replies use Home Assistant's [intents](https://github.com/OHF-Voice/intents)
  response templates. Spoken numbers come from [unicode-rbnf](https://github.com/rhasspy/unicode-rbnf).
