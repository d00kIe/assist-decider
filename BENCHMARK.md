# Decision model benchmark

Which small decision model understands home commands best, and what does it cost to run?
This page compares five models that can run locally, on 55 test sentences, in English and German.

Measured on 2026-10-07 on a MacBook Pro M4 Pro (Apple graphics, 48 GB), with `laya 0.3.26`,
`transformers 5.18.0` and `torch 2.14.1`. How to repeat it is at the [end](#repeat-the-benchmark).

## Short answer

- **Most accurate: Intern-Decision 2B.** It got 52 of 55 sentences right. With the confidence check at
  0.4 it got 51 right and 1 wrong, and that one mistake comes from a bug in our German word list,
  not from the model. It needs about 4.6 GB, which is **too much for a 4 GB graphics card**.
- **Best that fits a 4 GB card: Intern-Decision 0.8B** (50 of 55 right, 2.3 GB), then **Kev 0.8B**
  (48 of 55 right, 2.3 GB). Both are much more careful about their own answers than Laya: they report
  lower confidence on the same answers. With our usual confidence setting (0.4) they hand many correct
  answers to Home Assistant, so they need a lower setting.
- **English only: Laya English** is still very good (32 of 34) and by far the fastest.
- **Laya multilingual** makes the most mistakes in the new approach (10 of 55). Six of them are in
  sentences that name only a room, where it picks the wrong device or the wrong action.
- **Giving the model more context does not help.** Describing devices, putting numbers into the
  options, or letting the model split the sentence changed at most one sentence for the better.
- **Your original idea, asking the model about every device and then about every action, only works
  with Intern-Decision 2B** (23 of 32 whole sentences, against 2 to 8 for the others).

## What was tested

### The test home

The test home is the same one the automatic tests use (`server/tests/conftest.py`). It has 5 rooms
and 11 devices: 4 lights, 2 heating thermostats, living room blinds, a garage door, a front door
lock, a coffee maker and a TV. German names come from each device's alias, such as "Küchenlicht".

### The sentences

- **32 sentences that name a device or a room**, such as "turn on the kitchen light and turn off the
  hallway light" or "stell die Heizung Bad auf 22 Grad und schalte die Kaffeemaschine ein".
  20 are English and 12 German.
- **23 sentences that name only a room or nothing**, said to a voice satellite in a known room, such
  as "turn on the light" in the kitchen or "Licht aus" in the bedroom. 14 are English and 9 German.

Laya English only reads English, so it was tested on the 34 English sentences.

### The three ways of understanding a sentence

| Name in the tables | What it does |
|---|---|
| **Today's pipeline** | The code as it is now. Word lists split the sentence at "and"/"und", and remove actions that can't fit (for example "turn on" when "off" was said). The model then picks the action and the device from what is left. |
| **New approach** | Devices are found by matching your own device and room names from Home Assistant. When nothing is named, the speaker's room is used. The model then decides: (1) is it a command or a question, (2) which device in a named room is meant, (3) what should happen to each device, choosing only from actions that kind of device supports, and (4) which number from the sentence is the value. The on/off words are still used as a safety filter. |
| **Model-only tree** | The model is asked about every device ("does the user mean the kitchen light?"), then about the action for each device, then about the value. No word lists and no name matching. |

### The confidence check

Every answer from the model comes with a confidence between 0 (a pure guess) and 1 (certain). When
any answer for a sentence is below the **confidence check** setting, the sentence is handed to Home
Assistant's own assistant instead of being acted on. The server's default setting is 0.4.

### How to read the result cells

Each cell reads **right/total · handed to Home Assistant · wrong**. For example, `48/55 · 1 · **6** (1🔒)`
means 48 sentences were right, 1 was handed to Home Assistant and 6 were wrong. In the 1 marked 🔒, the
front door lock or the garage door would have done the wrong thing.

A sentence counts as right only when every device, action and value in it is right.

## Results

### Memory and speed

| Model | Parameters | Weights | Memory in use (graphics) | Peak RAM while loading | Per question |
|---|---:|---:|---:|---:|---:|
| Laya multilingual | 322 M | 1.2 GB | 1.2 GB | 2.5 GB | 13 ms |
| Laya English | 421 M | 1.6 GB | 2.0 GB | 2.7 GB | 22 ms |
| Kev 0.8B | 752 M | 1.4 GB | 2.3 GB | 5.3 GB | 60 ms |
| Intern-Decision 0.8B | 853 M | 1.6 GB | 2.3 GB | 0.7 GB | 209 ms |
| Intern-Decision 2B | 2213 M | 4.1 GB | 4.6 GB | 3.0 GB | 276 ms |

### Whole sentences: today's pipeline against the new approach

Confidence check at **0.0**:

| Model | Today's pipeline | New approach |
|---|---:|---:|
| Laya multilingual | 47/55 · 5 · **3** (1🔒) | 44/55 · 1 · **10** (1🔒) |
| Laya English | 32/34 · 0 · **2** | 32/34 · 1 · **1** |
| Kev 0.8B | 44/55 · 8 · **3** | 48/55 · 1 · **6** (1🔒) |
| Intern-Decision 0.8B | 49/55 · 2 · **4** (1🔒) | 50/55 · 1 · **4** (1🔒) |
| Intern-Decision 2B | 50/55 · 2 · **3** (1🔒) | 52/55 · 1 · **2** (1🔒) |

Confidence check at **0.4**:

| Model | Today's pipeline | New approach |
|---|---:|---:|
| Laya multilingual | 45/55 · 7 · **3** (1🔒) | 42/55 · 5 · **8** (1🔒) |
| Laya English | 28/34 · 5 · **1** | 31/34 · 2 · **1** |
| Kev 0.8B | 39/55 · 14 · **2** | 36/55 · 16 · **3** (1🔒) |
| Intern-Decision 0.8B | 46/55 · 6 · **3** (1🔒) | 33/55 · 21 · **1** (1🔒) |
| Intern-Decision 2B | 49/55 · 3 · **3** (1🔒) | 51/55 · 3 · **1** (1🔒) |

### New approach by sentence type and language (no confidence check)

| Model | Device named | Only a room / nothing | English | German |
|---|---:|---:|---:|---:|
| Laya multilingual | 27/32 · 1 · **4** (1🔒) | 17/23 · 0 · **6** | 28/34 · 1 · **5** | 16/21 · 0 · **5** (1🔒) |
| Laya English | 19/20 · 1 · **0** | 13/14 · 0 · **1** | 32/34 · 1 · **1** | 0/0 · 0 · **0** |
| Kev 0.8B | 28/32 · 1 · **3** (1🔒) | 20/23 · 0 · **3** | 30/34 · 1 · **3** | 18/21 · 0 · **3** (1🔒) |
| Intern-Decision 0.8B | 28/32 · 1 · **3** (1🔒) | 22/23 · 0 · **1** | 32/34 · 1 · **1** | 18/21 · 0 · **3** (1🔒) |
| Intern-Decision 2B | 30/32 · 1 · **1** (1🔒) | 22/23 · 0 · **1** | 33/34 · 1 · **0** | 19/21 · 0 · **2** (1🔒) |

### Confidence check: what each setting does to the new approach

| Model | check 0.0 | check 0.1 | check 0.2 | check 0.3 | check 0.4 | check 0.5 |
|---|---:|---:|---:|---:|---:|---:|
| Laya multilingual | 44/55 · 1 · **10** (1🔒) | 44/55 · 1 · **10** (1🔒) | 44/55 · 1 · **10** (1🔒) | 43/55 · 3 · **9** (1🔒) | 42/55 · 5 · **8** (1🔒) | 41/55 · 6 · **8** (1🔒) |
| Laya English | 32/34 · 1 · **1** | 32/34 · 1 · **1** | 32/34 · 1 · **1** | 31/34 · 2 · **1** | 31/34 · 2 · **1** | 30/34 · 3 · **1** |
| Kev 0.8B | 48/55 · 1 · **6** (1🔒) | 45/55 · 5 · **5** (1🔒) | 41/55 · 9 · **5** (1🔒) | 38/55 · 14 · **3** (1🔒) | 36/55 · 16 · **3** (1🔒) | 32/55 · 22 · **1** |
| Intern-Decision 0.8B | 50/55 · 1 · **4** (1🔒) | 48/55 · 3 · **4** (1🔒) | 45/55 · 7 · **3** (1🔒) | 40/55 · 13 · **2** (1🔒) | 33/55 · 21 · **1** (1🔒) | 28/55 · 27 · **0** |
| Intern-Decision 2B | 52/55 · 1 · **2** (1🔒) | 52/55 · 1 · **2** (1🔒) | 52/55 · 1 · **2** (1🔒) | 52/55 · 1 · **2** (1🔒) | 51/55 · 3 · **1** (1🔒) | 49/55 · 5 · **1** (1🔒) |

### Extra context for the model (no confidence check)

| Model | plain | + devices described | + numbers in options | + model splits sentence | all three |
|---|---:|---:|---:|---:|---:|
| Laya multilingual | 44/55 · 1 · **10** (1🔒) | 39/55 · 1 · **15** (1🔒) | 44/55 · 1 · **10** (1🔒) | 43/55 · 1 · **11** (1🔒) | 39/55 · 1 · **15** (2🔒) |
| Laya English | 32/34 · 1 · **1** | 31/34 · 1 · **2** | 31/34 · 1 · **2** | 32/34 · 1 · **1** | 30/34 · 1 · **3** |
| Kev 0.8B | 48/55 · 1 · **6** (1🔒) | 46/55 · 1 · **8** (1🔒) | 48/55 · 1 · **6** (1🔒) | 44/55 · 1 · **10** (1🔒) | 44/55 · 1 · **10** (1🔒) |
| Intern-Decision 0.8B | 50/55 · 1 · **4** (1🔒) | 50/55 · 1 · **4** (1🔒) | 50/55 · 1 · **4** (1🔒) | 49/55 · 1 · **5** (1🔒) | 48/55 · 1 · **6** (1🔒) |
| Intern-Decision 2B | 52/55 · 1 · **2** (1🔒) | 53/55 · 1 · **1** (1🔒) | 52/55 · 1 · **2** (1🔒) | 51/55 · 1 · **3** (1🔒) | 52/55 · 1 · **2** (1🔒) |

### The model-only tree, step by step

| Model | Devices, yes ≥ 0.5 | Devices, best cut-off | Devices, top-ranked (count known) | Action per device | Value | Vague change | Whole sentence | Wrong on lock/garage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Laya multilingual | 3/32 | 9/32 (at 0.80) | 21/32 | 35/45 | 9/11 | 1/5 | 2/32 | 12 |
| Laya English | 2/20 | 5/20 (at 0.60) | 13/20 | 26/29 | 7/7 | 2/3 | 2/20 | 7 |
| Kev 0.8B | 12/32 | 20/32 (at 0.65) | 29/32 | 38/45 | 10/11 | 4/5 | 8/32 | 3 |
| Intern-Decision 0.8B | 5/32 | 20/32 (at 0.60) | 28/32 | 38/45 | 11/11 | 1/5 | 3/32 | 7 |
| Intern-Decision 2B | 26/32 | 28/32 (at 0.60) | 30/32 | 42/45 | 11/11 | 4/5 | 23/32 | 2 |

### Time per sentence (median)

| Model | Today's pipeline | New approach |
|---|---:|---:|
| Laya multilingual | 15 ms | 36 ms |
| Laya English | 31 ms | 63 ms |
| Kev 0.8B | 155 ms | 214 ms |
| Intern-Decision 0.8B | 267 ms | 539 ms |
| Intern-Decision 2B | 352 ms | 716 ms |

### Every mistake of the new approach (no confidence check)

**Laya multilingual** (11)

- "open the blinds to 30 percent": wanted living_room_blinds:set_position=30, got handed to Home Assistant
- "dim the desk lamp to 20 and close the garage door": wanted desk_lamp:set_brightness=20, garage_door:close, got desk_lamp:turn_off, garage_door:close
- "set the thermostat to 22 degrees and the bathroom heating to 24": wanted living_room:set_temperature=22, bathroom:set_temperature=24, got living_room:set_temperature=22, bathroom:set_temperature=22
- "mach das Küchenlicht an und den Fernseher aus": wanted kitchen_ceiling:turn_on, tv:turn_off, got kitchen_ceiling:turn_on, tv:turn_on
- "sperr die Haustür ab": wanted front_door:lock, got front_door:unlock 🔒
- "open the blinds to 30 percent" (speaker in living_room): wanted living_room_blinds:set_position=30, got living_room_blinds:open
- "dim the light to 30 percent" (speaker in living_room): wanted living_room_floor:set_brightness=30, got living_room_floor:set_brightness
- "turn on the music" (speaker in living_room): wanted tv:turn_on, got living_room_blinds:open
- "fahr die Rollos auf 30 Prozent" (speaker in living_room): wanted living_room_blinds:set_position=30, got living_room_floor:turn_on
- "Licht aus" (speaker in bedroom): wanted desk_lamp:turn_off, got desk_lamp:query
- "mach es heller, 70 Prozent" (speaker in living_room): wanted living_room_floor:set_brightness=70, got living_room_blinds:set_position

**Laya English** (2)

- "open the blinds to 30 percent": wanted living_room_blinds:set_position=30, got handed to Home Assistant
- "open the blinds to 30 percent" (speaker in living_room): wanted living_room_blinds:set_position=30, got living_room_blinds:open

**Kev 0.8B** (7)

- "turn on the kitchen light and turn off the hallway light": wanted kitchen_ceiling:turn_on, hallway:turn_off, got kitchen_ceiling:turn_off, hallway:turn_off
- "open the blinds to 30 percent": wanted living_room_blinds:set_position=30, got handed to Home Assistant
- "set the thermostat to 22 degrees and the bathroom heating to 24": wanted living_room:set_temperature=22, bathroom:set_temperature=24, got living_room:set_temperature=24, bathroom:set_temperature=24
- "sperr die Haustür ab": wanted front_door:lock, got front_door:unlock 🔒
- "make it 23 degrees in here" (speaker in living_room): wanted living_room:set_temperature=23, got living_room:query
- "Licht aus" (speaker in bedroom): wanted desk_lamp:turn_off, got desk_lamp:query
- "mach es heller, 70 Prozent" (speaker in living_room): wanted living_room_floor:set_brightness=70, got living_room_floor:query

**Intern-Decision 0.8B** (5)

- "turn on the kitchen light and turn off the hallway light": wanted kitchen_ceiling:turn_on, hallway:turn_off, got kitchen_ceiling:turn_off, hallway:turn_off
- "open the blinds to 30 percent": wanted living_room_blinds:set_position=30, got handed to Home Assistant
- "mach das Küchenlicht an und den Fernseher aus": wanted kitchen_ceiling:turn_on, tv:turn_off, got kitchen_ceiling:turn_on, tv:turn_on
- "sperr die Haustür ab": wanted front_door:lock, got front_door:unlock 🔒
- "mach es heller, 70 Prozent" (speaker in living_room): wanted living_room_floor:set_brightness=70, got living_room_floor:query

**Intern-Decision 2B** (3)

- "open the blinds to 30 percent": wanted living_room_blinds:set_position=30, got handed to Home Assistant
- "sperr die Haustür ab": wanted front_door:lock, got front_door:unlock 🔒
- "mach es heller, 70 Prozent" (speaker in living_room): wanted living_room_floor:set_brightness=70, got living_room:set_temperature=70


## What the mistakes have in common

1. **"Sperr die Haustür ab" ("lock the front door") unlocks the door, with every model that reads German.** This is a bug
   in our German word list, not in the models: "ab" is listed as an "off" word, which means "unlock"
   for a lock, and "sperr" is missing from the "on" words. Fix this before anything else.
2. **"Open the blinds to 30 percent" with no room known is handed to Home Assistant on purpose.**
   Nothing in it matches a device or room name, and the test gives no speaker's room.
3. **One sentence that turns one thing on and another off** ("turn on the kitchen light and turn off
   the hallway light", "Küchenlicht an und den Fernseher aus") still fails for Laya multilingual,
   Kev and Intern-Decision 0.8B. Intern-Decision 2B gets both right.
4. **"Mach es heller, 70 Prozent"** ("make it brighter, 70 percent") fails with every model that reads German. Intern-Decision 2B
   chose to set the living room thermostat to 70 degrees. A range check in code (heating accepts
   5 to 35 °C) would refuse that. The real pipeline has such a check, but this benchmark does not
   apply it.

## What this means for the GeForce plan

| | Fits a 4 GB card? | Right (of 55), no check | Notes |
|---|---|---|---|
| Intern-Decision 2B | No, unless compressed to 8-bit (untested) | 52 | Best answers. Runs well on the Mac. |
| Intern-Decision 0.8B | Yes, about 2.3 GB | 50 | Needs a confidence setting of about 0.1 to 0.2 instead of 0.4. |
| Kev 0.8B | Yes, about 2.3 GB | 48 | Trained on English only. It also needs a lower confidence setting. |
| Laya multilingual | Yes, about 1.2 GB | 44 | Fastest, but makes the most mistakes. |

On the Mac, the Qwen-based models (Kev and Intern-Decision) take 60 to 280 ms per question. Two
speed-up libraries (`flash-linear-attention`, `causal-conv1d`) only exist for NVIDIA cards, so on
the Mac they run slow fallback code. On an NVIDIA card they should be much faster. Intern-Decision's
model card reports 34 ms per question on an RTX 4090. That has not been measured here.

## Limits of this benchmark

- **55 sentences and one test home.** A difference of one or two sentences between models is noise.
- **The confidence settings in the tables were tried on these same sentences.** A setting that looks
  best here needs confirming on new sentences before it goes into the configuration.
- **Kev 0.8B was trained on English only.** Its German results are measured here, but its makers
  don't claim German support.
- **Time and memory were measured on the Mac only.** "Memory in use" is what the Apple graphics
  driver reports. "Peak RAM while loading" is the most memory the process used: Kev briefly needs
  more because it merges its add-on at full precision before shrinking.

## The models

| Model | Made by | Built on | License |
|---|---|---|---|
| [Laya multilingual](https://huggingface.co/convaiinnovations/laya-multilingual) | ConvAI Innovations | mmBERT-base | Apache-2.0 |
| [Laya English](https://huggingface.co/convaiinnovations/laya) | ConvAI Innovations | ModernBERT-large | Apache-2.0 |
| [Kev 0.8B](https://huggingface.co/jaredpalmer/kev-0.8b) | Jared Palmer | Qwen3.5-0.8B-Base + LoRA add-on + scoring head | Apache-2.0 |
| [Intern-Decision 0.8B](https://huggingface.co/internlm/Intern-Decision-0.8B) | InternLM | Qwen3.5-0.8B | Apache-2.0 |
| [Intern-Decision 2B](https://huggingface.co/internlm/Intern-Decision-2B) | InternLM | Qwen3.5-2B | Apache-2.0 |

Every download is pinned to a reviewed commit (`server/assist_decider_server/providers.py`). For Kev
and Intern-Decision, the server does not run any code from the download. It rebuilds the prompt
format itself. The Kev version gives the same probabilities as Kev's own code to within 0.003.

## Repeat the benchmark

From the `server` folder:

```bash
# answers for every sentence, saved to tests/eval/results/<model>.json (slow models: 5-15 minutes)
uv run python tests/eval/probe_device_tree.py multilingual english kev-0.8b \
    intern-decision-0.8b intern-decision-2b --tree --out=tests/eval/results

# memory and speed, one process per model
for m in multilingual english kev-0.8b intern-decision-0.8b intern-decision-2b; do
    uv run python tests/eval/measure_memory.py $m --out=tests/eval/results
done

# the tables on this page
uv run python tests/eval/report.py
```

To switch the server to another model, set `model = "intern-decision-2b"` in the config file, or
start it with `--model intern-decision-2b`.
