# How Assist Decider works

This explains, in plain words:

1. [What Home Assistant can do by voice](#1-what-home-assistant-can-do-by-voice): the kinds of things it controls, the actions it knows, and the values each action accepts.
2. [What the model is asked](#2-what-the-model-is-asked): the four questions, and how much the model can read at once.
3. [What happens when you speak](#3-what-happens-when-you-speak): who decides what, step by step, and what goes back to Home Assistant.

Everything here was checked against the code in this repo and Home Assistant 2026.9.4. Why the server works this way, with measurements, is in [BENCHMARK.md](BENCHMARK.md).

---

## A few words first

| Word | What it means here |
|---|---|
| **Entity** | One thing Home Assistant can see or control: a lamp, a plug, a thermostat, a temperature sensor. Each has an ID such as `light.kitchen` and a friendly name such as "Kitchen Light". |
| **Kind** (Home Assistant calls it *domain*) | The part of the ID before the dot: `light`, `switch`, `cover`… It says what sort of thing it is. |
| **Sub-kind** (*device class*) | A finer label inside a kind. A `cover` can be a `blind`, `garage`, `window`…; a `binary_sensor` can be a `door`, `motion`… |
| **Area** | A room. **Floor** is a group of rooms. |
| **Exposed** | You ticked "allow voice assistants to see this" in Home Assistant. Nothing else is ever sent or controlled. |
| **Action** (Home Assistant calls it *intent*) | A named thing Home Assistant knows how to do, such as `HassTurnOn`. |
| **Parameter** (Home Assistant calls it *slot*) | A value an action needs: which device, which room, what brightness. |
| **Decision model** | A small AI model, such as Intern-Decision or Laya. You give it a question plus a fixed list of answers, and it says how likely each answer is. It cannot write text or invent answers. |
| **Confidence** | How sure the model is about an answer: 0 is a pure guess, 1 is certain. |
| **Token** | A word piece. Models count text in tokens. One English word is about 1–1.5 tokens, a long German word can be 3–5. |

---

## 1. What Home Assistant can do by voice

### 1.1 Kinds of things

Home Assistant has many kinds of entities. These are the ones voice actions work with. "Used today" means Assist Decider can already produce actions for them.

| Kind | What it is | Typical sub-kinds | Used today |
|---|---|---|---|
| `light` | Lamps, LED strips | – | ✅ on/off, brightness |
| `switch` | Plugs, relays | `outlet`, `switch` | ✅ on/off |
| `input_boolean` | A virtual on/off switch you made in Home Assistant | – | ✅ on/off |
| `fan` | Fans | – | ✅ on/off (speed not yet) |
| `cover` | Blinds, shutters, curtains, garage doors, gates | `awning`, `blind`, `curtain`, `damper`, `door`, `garage`, `gate`, `shade`, `shutter`, `window` | ✅ open/close, position |
| `valve` | Water and gas valves, sprinklers | `water`, `gas` | ✅ open/close, position |
| `lock` | Door locks | – | ✅ lock/unlock, **only by exact name** |
| `climate` | Thermostats, heating, air conditioning | – | ✅ on/off, set temperature, ask temperature |
| `media_player` | TVs, speakers | `tv`, `speaker`, `receiver` | ✅ on/off (pause, volume… not yet) |
| `humidifier` | Humidifiers, dehumidifiers | `humidifier`, `dehumidifier` | ✅ on/off (level and mode not yet) |
| `scene` | A saved set of device settings | – | ✅ turn on |
| `script` | A saved sequence of steps | – | ✅ turn on |
| `automation` | A rule that runs by itself | – | ✅ on/off |
| `sensor` | Measures something: temperature, humidity, power… | `temperature`, `humidity`, `power`… | ✅ ask state |
| `binary_sensor` | A yes/no sensor: door open, motion seen | `door`, `window`, `motion`, `opening`… | ✅ ask state |
| `weather` | Weather forecast | – | ✅ ask state |
| `vacuum` | Robot vacuums | – | ❌ |
| `lawn_mower` | Robot mowers | – | ❌ |
| `todo` | To-do and shopping lists | – | ❌ |
| `alarm_control_panel` | Alarm systems | – | ❌, and never guessed |

Locks, alarm panels and covers of sub-kind `garage`, `gate` or `door` are **safety-sensitive**. Assist Decider only acts on them when you say their exact name or alias. When you name only a room, they are never picked.

### 1.2 What is sent about each thing

Home Assistant sends the decision server only names and IDs, never a full picture of your home:

| For | Sent | Limits |
|---|---|---|
| Each exposed entity | ID, friendly name, up to 10 aliases, room ID, sub-kind | up to 3000 entities; names up to 100 characters |
| Each area | ID, name, up to 10 aliases, floor ID | up to 500 areas |
| Each floor | ID, name, up to 10 aliases | up to 50 floors |

**No states are sent**: not whether a light is on, not lock states, not sensor values.

### 1.3 Every voice action Home Assistant has

Most actions that control a device share the same **"which device" parameters**:

| Parameter | Type | Meaning |
|---|---|---|
| `name` | text | One device, by name. |
| `area` | text | A room. |
| `floor` | text | A floor. |
| `domain` | list of text | Only this kind, e.g. `["light"]`. |
| `device_class` | list of text | Only this sub-kind, e.g. `["blind"]`. |
| `preferred_area_id` / `preferred_floor_id` | text | The room/floor the voice satellite is in. Used to break ties. |

At least one of `name`, `area` or `floor` is required, unless stated otherwise below.

#### Actions Assist Decider produces today

| Action | What it does | Its own parameters | Type and range | Notes |
|---|---|---|---|---|
| `HassTurnOn` | Turn on, open, activate. **Locks a lock.** Runs a scene or script. | – | – | |
| `HassTurnOff` | Turn off, close, deactivate. **Unlocks a lock.** | – | – | |
| `HassLightSet` | Change a light | `brightness` | whole number 0–100 (percent) | `color` (a colour name) and `temperature` (kelvin, positive whole number) also exist. Not used yet. |
| `HassClimateSetTemperature` | Set the target temperature | `temperature` | any decimal number in Home Assistant. **We only send 5–35 °C, rounded to the nearest 0.5.** | |
| `HassSetPosition` | Move a cover or valve | `position` | whole number 0–100 (percent open) | |
| `HassGetState` | Ask about a device | `state` (optional) | list of text, e.g. `["on"]` | Home Assistant also accepts area + kind ("are any lights on in the kitchen?"). We only send one named device for now. |
| `HassClimateGetTemperature` | Ask how warm it is | – | – | Used for every question about a thermostat. |

Every action we send names exactly **one device** (`name`), never a whole room.

#### Actions Home Assistant has that we do not produce yet

| Action | What it does | Its own parameters (type, range) |
|---|---|---|
| `HassToggle` | Flip on ↔ off | – |
| `HassStopMoving` | Stop a cover or valve | – |
| `HassFanSetSpeed` | Fan speed | `percentage`: whole number 0–100 |
| `HassSetVolume` | Volume | `volume_level`: whole number 0–100 |
| `HassSetVolumeRelative` | Louder/quieter | `volume_step`: `"up"`, `"down"` or a whole number −100 to 100 |
| `HassMediaPause` / `HassMediaUnpause` / `HassMediaNext` / `HassMediaPrevious` | Media controls | – |
| `HassMediaPlayerMute` / `HassMediaPlayerUnmute` | Mute | `is_volume_muted`: yes/no (optional) |
| `HassMediaSearchAndPlay` | "Play some jazz" | `search_query`: **free text**; `media_class`: one of Home Assistant's media types (optional) |
| `HassHumidifierSetpoint` | Humidity target | `name` (required); `humidity`: whole number 0–100 |
| `HassHumidifierMode` | Humidifier mode | `name` (required); `mode`: text |
| `HassVacuumStart` / `HassVacuumReturnToBase` | Vacuum | – |
| `HassVacuumCleanArea` | Vacuum one room | `area` (required) |
| `HassLawnMowerStartMowing` / `HassLawnMowerDock` | Mower | – |
| `HassStartTimer` | Start a timer | at least one of `hours`, `minutes`, `seconds`: positive whole numbers; `name`: text (optional); `conversation_command`: **free text** (optional) |
| `HassCancelTimer`, `HassPauseTimer`, `HassUnpauseTimer`, `HassTimerStatus` | Timer control | which timer: `start_hours`/`start_minutes`/`start_seconds` (positive whole numbers), `name` or `area` (text) |
| `HassIncreaseTimer` / `HassDecreaseTimer` | Add or remove time | `hours`/`minutes`/`seconds` plus which timer, as above |
| `HassCancelAllTimers` | Cancel all timers | `area` (optional) |
| `HassGetCurrentTime` / `HassGetCurrentDate` | Time and date | – |
| `HassGetWeather` | Weather | `name` (optional) |
| `HassListAddItem` / `HassListCompleteItem` / `HassListRemoveItem` | To-do lists | `item`: **free text**; `name`: list name |
| `HassShoppingListAddItem` / `HassShoppingListCompleteItem` / `HassShoppingListLastItems` | Shopping list | `item`: **free text** |
| `HassBroadcast` | Announce on all speakers | `message`: **free text** |
| `HassNevermind` | "Never mind" | – |
| `HassRespond` | Just say something | `response`: text |

Anything marked **free text** needs words copied out of your sentence. The models can only pick from a list, so these need a different tool (see PLAN.md, "Later").

### 1.4 What Home Assistant lets the server ask for

Home Assistant does not trust the server. It only runs actions from this allow-list, with these parameters, and only for devices and rooms it sent:

| Action | Parameters allowed |
|---|---|
| `HassTurnOn`, `HassTurnOff` | `name`, `area`, `domain` |
| `HassLightSet` | `name`, `area`, `domain`, `brightness` |
| `HassClimateSetTemperature` | `name`, `area`, `temperature` |
| `HassSetPosition` | `name`, `area`, `domain`, `position` |
| `HassGetState` | `name`, `area`, `domain`, `state` |
| `HassClimateGetTemperature` | `name`, `area` |

The server always sends **IDs** (`light.kitchen`, `kitchen`), never free names, so there is no doubt which device is meant.

---

## 2. What the model is asked

### 2.1 The four questions

The model only ever answers multiple-choice questions about your sentence. There are four, and each is short:

| Question | When it is asked | Answers offered |
|---|---|---|
| "Is the user giving a command or asking a question?" | Once per sentence | a command · a question |
| "Which device does the user mean?" | Only when you named a room (or nothing), and that room has more than one device that fits | the room's devices, e.g. "Kitchen Light (light) in Kitchen"; at most 10 |
| "What does the user want with the Kitchen Light?" | Once per device | only what that kind of device can do, e.g. for a light: turn on · turn off · set the brightness · only asks how it is |
| "Which value should the Kitchen Light be set to?" | Only when the action needs a number | the numbers you said · "no value given" |

German sentences get the same questions in German. The model sees your sentence each time (the *state*), never your list of devices or their states.

### 2.2 What one question looks like

```
question: What does the user want with the Kitchen Light?
answers:  turn_on:        turn on, switch on, start
          turn_off:       turn off, switch off, stop
          set_brightness: set the brightness to a value
          query:          only asks how it is, changes nothing
state:    {"utterance": "turn off the kitchen light"}
```

The model answers with a likelihood for every answer, for example `turn_off` 0.91, `turn_on` 0.03, and so on.

### 2.3 How much the model can read at once

Laya reads at most **512 tokens** (`english`) or **1024 tokens** (`multilingual`) per question. The question and its answers may use at most 192 or 256 of them, and each answer at most 48. The other models are built on Qwen3.5 and read much longer texts, so for them these limits don't matter.

The biggest question is "which device?" with 10 devices. With typical names that is about 150–170 tokens, inside even the tightest limit. That is one reason a room may offer at most 10 devices. Each question is read on its own: questions don't add up, and the model keeps no memory between them. Remembering the previous command ("turn *it* off") is done by the server code.

---

## 3. What happens when you speak

### 3.1 The whole trip

```mermaid
sequenceDiagram
    autonumber
    actor You
    participant Sat as Voice satellite
    participant HA as Home Assistant
    participant Int as Assist Decider<br/>(inside Home Assistant)
    participant Srv as Decision server<br/>(your Mac / PC)
    participant M as Decision model

    You->>Sat: "Turn on the kitchen light and turn off the hallway light"
    Sat->>HA: audio
    HA->>HA: speech to text (Whisper)
    HA->>Int: the text, language, which satellite
    Int->>Int: collect exposed devices, rooms, settings
    Int->>Srv: one request
    Srv->>Srv: find device and room names, read numbers
    loop a few short questions
        Srv->>M: one question + list of answers
        M-->>Srv: likelihood of each answer
    end
    Srv-->>Int: list of proposed actions, or "hand it off"
    Int->>Int: check every action against the allow-list
    Int->>HA: run each action, in order
    HA-->>Int: result of each
    Int->>HA: one spoken reply
    HA->>Sat: text to speech (Piper)
    Sat->>You: "Turned on the light. Turned off the light."
```

The server can **only suggest**. It has no password for Home Assistant. Home Assistant checks every suggestion again and runs it with the normal voice-assistant rules.

### 3.2 Who decides what

| Decision | Who decides | How |
|---|---|---|
| Is it an "if…" sentence? | **Code** | Words like "if/wenn/falls". Not supported: the whole sentence is handed off (`conditional`). |
| Which numbers were said | **Code** | "einundzwanzig komma fünf Grad" → 21.5 °. "fifty percent" → 50 %. |
| Which devices and rooms were named | **Code** | Exact match against names and aliases, including German compounds ("Wohnzimmerlicht" → room "Wohnzimmer"). |
| Nothing named: which devices then | **Code** | The previous command's devices ("turn it off"), else the satellite's room. |
| Command or question | **Model** | Asked once. A question can only *ask*; a command can never just *ask*. |
| Which device in a room | **Model**, from a list made by code | Code offers only devices that can take the number you said, and never locks or garage doors. One device left: no question. |
| What to do with each device | **Model**, from a list made by code | Code offers only what that kind of device can do, and rules out what contradicts your words. |
| The value (brightness, temperature…) | **Model** picks one of the numbers you said | Code then checks it fits: 0–100 % for brightness and position, 5–35 ° for temperature. |
| Is the model sure enough? | **Code** | The confidence check, see 3.5. |
| May this action run on this device? | **Home Assistant** | Allow-list check, then Home Assistant's own exposure check. |
| What to say back | **Home Assistant** | Its own built-in reply sentences, in English or German. |
| What to do with a handed-off sentence | **Home Assistant** | Hand it to a fallback agent you chose, or say "Sorry, I couldn't understand that". |

### 3.3 Inside the server, step by step

```mermaid
flowchart TD
    A["Sentence from Home Assistant"] --> B["Normalize text<br/>lowercase, ä→ae, ß→ss, 21,5→21.5"]
    B --> C["Find device and room names, read numbers"]
    C --> D{"'if …' sentence?"}
    D -- yes --> X["Hand off: conditional"]
    D -- no --> E["1. Which devices?<br/>see 3.4"]
    E -- "none found" --> Y["Hand off: no_target"]
    E --> F["2. Ask: command or question?"]
    F --> G["3. For each named room:<br/>ask which device"]
    G --> H["4. For each device:<br/>ask what to do"]
    H --> I["5. Action needs a number?<br/>ask which one, check it fits"]
    I --> K{"Every answer sure enough?"}
    K -- no --> Z["Hand off: low_confidence"]
    K -- yes --> L["Actions, one per device<br/>remember the devices for 60 s"]
    X --> R["Answer to Home Assistant"]
    Y --> R
    Z --> R
    L --> R
```

### 3.4 How the devices are chosen

The server tries certain ways first. The model only picks when a room was named.

```mermaid
flowchart TD
    S["Sentence"] --> A{"A device name<br/>was said?"}
    A -- "yes" --> DEV["Use that device<br/>(same name in several rooms:<br/>the room said, then the satellite's room)"]
    S --> R{"A room name<br/>was said?"}
    R -- "yes, and one of its<br/>devices is named too" --> SKIP["Nothing more:<br/>'the light in the kitchen'"]
    R -- "yes" --> ROOM["The room's devices<br/>without locks and garage doors"]
    A -- "no" --> N{"Neither said"}
    R -- "no" --> N
    N --> P{"A previous command within 60 s,<br/>and no kind of device said?<br/>('turn it off', not 'turn on the light')"}
    P -- yes --> PREV["The same devices as last time"]
    P -- no --> SAT{"Satellite has a room?"}
    SAT -- yes --> ROOM
    SAT -- no --> ERR["Hand off: no_target"]
    ROOM --> FIT["A number said? Keep only devices<br/>that can take it ('fit')"]
    FIT --> ONE{"One device left?"}
    ONE -- yes --> USE["Use it"]
    ONE -- no --> ASK["Ask the model:<br/>'Which device does the user mean?'"]
```

### 3.5 What to do with each device

For every device, the server builds the list of actions the model may choose:

1. **Only what that kind of device can do.** A light: turn on, turn off, set brightness, ask. A blind: open, close, set position, ask. A lock: lock, unlock, ask. A sensor: ask.
2. **Command or question.** A question allows only "ask"; a command rules "ask" out.
3. **Your on/off words.** "off", "aus", "close", "zu"… rule out "turn on" and "open". Locks use their own words: "lock", "ab", "zu" lock it; "unlock", "auf", "öffne" unlock it. When you say both an on and an off word, each device follows the one **nearest to its name**: in "turn on the kitchen light and turn off the hallway light", the kitchen light gets "on" and the hallway light "off".
4. **Actions Home Assistant has.** An action whose intent Home Assistant doesn't have is ruled out.

The model always sees every action of that kind of device, because models get worse when answers are removed. Ruled-out answers are dropped afterwards, and the rest are scaled back up to 100 %.

**How sure is it?** From the likelihoods, the server computes a confidence for the chosen answer:
`confidence = (number of answers × top likelihood − 1) ÷ (number of answers − 1)`.
0 means "no better than a random pick", 1 means "certain". Example: 4 answers, top one at 70 % → (4 × 0.7 − 1) ÷ 3 = **0.6**.

**The confidence check.** If any answer in the sentence is below your threshold, **nothing runs** and the whole sentence is handed off. A sentence is done completely or not at all. The default threshold is 0.4 (English) and 0.5 (German); each model has its own best value, see the README's "Which model?".

A sentence needs **1 + one per device** questions, plus one per room and one per number. One question takes about 15–25 ms with Laya and 0.06–0.4 s with the other models on an Apple M-series GPU. The server answers one request at a time. If more than 4 requests are waiting, it answers "busy".

### 3.6 What the server sends back

```json
{
  "status": "ok",
  "actions": [
    {"intent": "HassTurnOn", "slots": {"name": "light.kitchen"},
     "segment": "Turn on the kitchen light and turn off the hallway light", "confidence": 0.62},
    {"intent": "HassTurnOff", "slots": {"name": "light.hallway"},
     "segment": "Turn on the kitchen light and turn off the hallway light", "confidence": 0.62}
  ],
  "unresolved": [],
  "reason": null,
  "trace_id": "3f9a1c2b7d10",
  "elapsed_ms": 1210.4
}
```

| Field | Meaning | Limits |
|---|---|---|
| `status` | `ok` when there are actions, otherwise `escalate` ("hand it off") | |
| `actions` | What to run, in order: one per device. `slots` are the parameters, always with IDs. `confidence` is the lowest of all answers in the sentence. | up to 10 actions |
| `segment` | The sentence, passed to Home Assistant's intent handlers | |
| `unresolved` | The sentence, when it was handed off | |
| `reason` | Why it was handed off, see below | |
| `trace_id`, `elapsed_ms` | For finding the request in the live log | |

**Reasons**, in plain words:

| Reason | Meaning |
|---|---|
| `low_confidence` | The model was not sure enough about one of its answers. |
| `no_target` | No device or room name was recognized, and the satellite has no room. |
| `too_many_devices` | More than 10 devices to act on, or a room with more than 10 devices and no kind of device said. |
| `no_action` | Everything a device can do was ruled out, e.g. "lock" for a light. |
| `missing_value` | "Dim the light", but no number was said, or the model chose "no value". |
| `value_not_possible` | The number doesn't fit the action, e.g. 70 degrees for a thermostat. |
| `conditional` | An "if…" sentence. These are not supported. |
| `unsupported_language` | The loaded model does not speak this language. |
| `no_exposed_entities` | Nothing is exposed to voice assistants. |

### 3.7 What Home Assistant does with the answer

```mermaid
flowchart TD
    R["Answer from the server"] --> E{"status = escalate?<br/>or anything not understood<br/>and a fallback agent is set?"}
    E -- yes --> FB{"Fallback agent set?"}
    FB -- yes --> FA["Hand the whole sentence to it<br/>(e.g. Home Assistant's own agent or an AI chat agent)"]
    FB -- no --> SORRY["Say 'Sorry, I couldn't understand that'"]
    E -- no --> V["Check each action:<br/>allowed action? allowed parameters?<br/>device or room that was sent?"]
    V -- fails --> DROP["Drop it and write a warning to the log"]
    V -- ok --> RUN["Run it through Home Assistant's own handler<br/>(same rules as any voice command)"]
    RUN --> SAY["Build the reply from Home Assistant's<br/>built-in sentences (EN/DE)"]
    SAY --> ALL["Join all replies<br/>+ 'I didn't understand the rest' if needed"]
```

If the server cannot be reached, or does not answer within **10 seconds**, Home Assistant says "the decision server is not reachable", or uses the fallback agent if one is set.

### 3.8 What Home Assistant sends to the server

| Field | Meaning | Type and limits |
|---|---|---|
| `protocol_version` | Must match on both sides | always `3` |
| `text` | What you said | 1–500 characters |
| `language` | | `en` or `de` |
| `satellite_area_id` | Room of the satellite you spoke to | room ID, optional |
| `context_id` | A scrambled ID of the satellite (or chat). Used to remember the last command. | 1–64 characters, optional |
| `intents` | Which actions this Home Assistant has | up to 300 names |
| `home` | Exposed devices, rooms, floors (see 1.2) | |
| `options.confidence_threshold` | How sure the model must be | 0–1; default 0.4 EN, 0.5 DE |
| `options.memory_seconds` | How long "turn it off" refers to the last command | 0–3600; default 60; 0 = off |

Unknown fields are refused. There is no authentication: the server is meant for a closed home network.

---

## 4. In one picture

```mermaid
flowchart LR
    subgraph HA["Home Assistant: owns the house"]
        H1["Knows devices, rooms, states"]
        H2["Checks and runs actions"]
        H3["Speaks the reply"]
    end
    subgraph SRV["Decision server: understands the sentence"]
        S1["Code: names, numbers,<br/>what each device can do,<br/>on/off words, memory"]
        S2["Model: command or question?<br/>which device? what to do?<br/>which number?"]
    end
    H1 -- "names and IDs" --> S1
    S1 <-->|"one short question,<br/>at most 10 answers"| S2
    S1 -- "suggested actions with IDs" --> H2
    H2 --> H3
```

- **Home Assistant** knows the house and has the final say.
- **Code on the server** does everything that has a clear rule: matching names, reading numbers, listing what each device can do, applying your on/off words, remembering the last command.
- **The model** answers short multiple-choice questions about your sentence, and only from answers the code allows. It never sees your whole home.
