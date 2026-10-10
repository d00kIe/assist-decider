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
| All of a kind, in a room or on a floor | "turn on all the lights in the living room", "close the blinds on the second floor" | "Schalte alle Lichter im Wohnzimmer ein" |
| Another word ending, or one word of a name | "turn off the left light but turn on the right one" | "Mach das linke Licht aus" |
| Nothing named: the satellite's room | "turn on the light", "it's too dark in here" | "Licht an" |
| Follow-ups, within a minute | "turn it off" | "Mach es an" |

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

#### With Docker (Linux, Windows)

Download [deploy/compose.yaml](deploy/compose.yaml), choose the model (see "Choose a model")
and start it:

```bash
curl -O https://raw.githubusercontent.com/d00kIe/assist-decider/main/deploy/compose.yaml
echo MODEL=intern-decision-0.8b > .env
docker compose up -d
docker compose logs -f    # wait for "Ready: provider=…"
```

The file fixes the server version (`REF`, a commit) and the base image, and the packages are
installed from the server's `uv.lock`, so every restart runs the same code. The first start
downloads PyTorch (about 5 GB) and the model into `./data`. After that, starting takes seconds.
To update, download `compose.yaml` again and run `docker compose up -d`. On `main`, `REF` is
always the latest server commit that passed CI.

The file uses an NVIDIA card. On Linux this needs the NVIDIA Container Toolkit; on Windows,
Docker Desktop with WSL 2 and a current NVIDIA driver. The log then shows `device=cuda`. Without
an NVIDIA card, delete the `deploy:` block and the model runs on the CPU.

Docker on macOS can't use the Apple GPU, so the model would run on the CPU. On a Mac, use uv.

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

To keep the server running:

- **macOS:** [deploy/assist-decider.plist](deploy/assist-decider.plist) starts it at login and
  restarts it if it stops. Set the model in it, then load it. The log goes to
  `~/Library/Logs/assist-decider.log`.

  ```bash
  curl -o ~/Library/LaunchAgents/assist-decider.plist https://raw.githubusercontent.com/d00kIe/assist-decider/main/deploy/assist-decider.plist
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/assist-decider.plist
  ```

- **Windows:** a scheduled task that starts it at login in a console window, without a time
  limit, and restarts it if it stops (PowerShell):

  ```powershell
  $action = New-ScheduledTaskAction -Execute "$env:USERPROFILE\.local\bin\assist-decider.exe" -Argument "--host 0.0.0.0 --model intern-decision-0.8b"
  $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  Register-ScheduledTask assist-decider -Action $action -Trigger (New-ScheduledTaskTrigger -AtLogOn) -Settings $settings
  ```

- **Linux:** a systemd unit.

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
in Home Assistant (next step). The numbers come from [BENCHMARK.md](BENCHMARK.md), measured on an
M4 Pro Mac: 65 sentences the server was tuned on, and 46 unseen ones, in English and German.

| `--model` | Languages | Memory | Time per sentence | Threshold | Right · handed off · wrong, tuned (65) | Unseen (46) |
|---|---|---|---|---|---|---|
| **`d1-3b`** (Liquid AI) | English, German | 6.8 GB | 0.66 s | 0.0 | **63 · 2 · 0** | **38 · 2 · 6** |
| `intern-decision-2b` | English, German | 4.6 GB | 0.71 s | 0.0 | 60 · 2 · 3 | 37 · 4 · 5 |
| **`intern-decision-0.8b`** | English, German | 2.3 GB | 0.59 s | **0.3** | 49 · 12 · 4 | 26 · 14 · 6 |
| `d1-omni-600m` (Liquid AI) | English, German | 1.0 GB | 0.12 s | 0.3 | 37 · 27 · 1 | 24 · 15 · 7 |
| `multilingual` (Laya, the default) | English, German | 1.2 GB | 59 ms | 0.5 | 40 · 19 · 6 | 16 · 18 · 12 |
| `english` (Laya) | English | 2.0 GB | 0.11 s | 0.0 | 34 · 1 · 4 (of 39) | 11 · 2 · 9 (of 22) |

"Handed off" means the sentence goes to the fallback agent instead of being acted on.

- **8 GB of memory or more** (a Mac, or a larger graphics card): use `d1-3b`. It is the most
  accurate by far. A threshold doesn't improve it, so leave it at 0.
- **Up to 4 GB of graphics memory:** use `intern-decision-0.8b` with a threshold of 0.3.
- `d1-omni-600m` is small and fast and rarely wrong, but hands off many plain commands.
- Laya is the fastest, but when it is wrong it is usually sure, so a threshold can't catch it.
- Common mistakes for most models: "if …" and "at 7 in the morning" sentences done right away,
  and German "… aus" at the end of a sentence that names one device ("Licht im Flur aus") read
  as "on".

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
| Model | the server's | Switches the server's model. The server unloads the current model before loading the new one (a model not downloaded yet takes minutes); if the new one fails, it reloads the old one and the error is shown. The server remembers the choice across restarts, until you change its `model` setting. |

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
| `state_file` | | `~/.local/state/assist-decider/state.json` | the model last chosen in Home Assistant |
| `max_pending`, `max_body_bytes`, `log_buffer` | | `4`, `1 MiB`, `2000` | queue size, request size limit, live-log length |

## Security and privacy

- **There is no authentication.** Anyone who can reach the port can send requests and read the
  live log. Use it on a closed home network only, never on the internet. Firewall the port if
  your LAN has untrusted devices, and use TLS or a VPN if you want encryption.
- **The server holds no Home Assistant credentials.** It only *proposes* actions. Home Assistant
  checks each one (allowed intent, exposed device, allowed slots) before running it.
- **Locks and garage doors** are only used when you say their name (another word ending counts).
  A room or floor command never picks them, "all the covers" never includes them, and changing
  one needs the model to be sure (0.5 or more) whatever your threshold.
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

- **German "… aus" at the end of a sentence** that names one device is often read as "on". The
  server has no word lists; the model decides, and the small models get this wrong.
- **"If …" / "wenn …" sentences and times ("at 7 in the morning") are recognized only in
  part.** When the model is sure, they go to the fallback agent; otherwise the action runs right
  away, without its condition.
- **"This floor" / "the whole house"** without the floor's name is only understood when the
  model is very sure, and only with a satellite in a known room. Floor names ("Obergeschoss")
  always work.
- Questions about all devices of a kind ("are all the lights off?") go to the fallback agent.
- Not supported yet: colors, timers, media controls, fan speed, volume, shopping lists.
- No follow-up questions ("which light?").

## Development

```bash
cd server && uv sync && uv run pytest                    # server tests (-m slow: real models)
cd server && uv run python tests/eval/benchmark.py d1-3b intern-decision-0.8b
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
