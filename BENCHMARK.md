# Decision model benchmark

Which small decision model understands home commands best, and what does it cost to run? This page
compares six models that run locally, on 65 test sentences in English and German.

Measured on 2026-10-09 on a MacBook Pro M4 Pro (Apple graphics, 48 GB), with `laya 0.3.26`,
`transformers 5.18.0` and `torch 2.14.1`, through the server's own code
(`server/tests/eval/benchmark.py`). How to repeat it is at the [end](#repeat-the-benchmark).

## Short answer

- **Most accurate: d1-3B** (Liquid AI). It gets 54 of the 55 original sentences right, gets none
  wrong, and hands the last one off on purpose. It takes 0.3 s per sentence on the Mac, which is
  faster than Intern-Decision 0.8B. But it needs about **6.8 GB**, so it doesn't fit a 4 GB card.
- **Best for a 4 GB card: still Intern-Decision 0.8B** (53 of 55, 2.3 GB). With a check of 0.2 it
  makes no mistakes on the original sentences.
- **Smallest and fastest new model: d1-omni-600M** (1.0 GB, 51 ms per sentence). Its accuracy is
  about Laya multilingual's (47 of 55). It mixes up "set" and "turn on" in some German sentences.
- **The 10 new sentences show what the pipeline can't do yet.** "All the lights in the living
  room", "all the lights on this floor", "all covers on the second floor" and "the left light …
  the right one" fail with every model. A room still means one device, floors aren't targets yet,
  and "the right one" isn't a name. Worse, most of these aren't handed off: one light is switched
  instead of all of them. Only the polite "can you turn on the light please" works.
- Kev 0.8B and H2O-Lightning 4B were removed from the server on 2026-10-09. Their results are in
  this page's git history.

## What was tested

### The test home

The 55 original sentences use the home of the automatic tests (`server/tests/conftest.py`). It has
5 rooms and 11 devices: 4 lights, 2 heating thermostats, living room blinds, a garage door, a front
door lock, a coffee maker and a TV. German names come from each device's alias, such as
"Küchenlicht".

The 10 new sentences use the same home with additions (`HOME_FLOORS` in `benchmark.py`): two
floors (the bedroom, an office and a kids' room upstairs), a left and a right light in the living
room, and blinds in the office and kids' room. The original 55 keep the old home, so their results
stay comparable with earlier runs.

### The sentences

- **32 that name a device or a room**, such as "turn on the kitchen light and turn off the hallway
  light" or "stell die Heizung Bad auf 22 Grad und schalte die Kaffeemaschine ein" (20 English, 12
  German).
- **23 that name only a room or nothing**, said to a voice satellite in a known room, such as "turn
  on the light" in the kitchen or "Licht aus" in the bedroom (14 English, 9 German).
- **10 new ones** (5 English, 5 German): "can you turn on the light please", "turn on all the
  lights in the living room", "turn off the left light but turn on the right one", "turn on all the
  lights on the floor" (meaning the speaker's floor) and "close all the covers on the second
  floor".

Laya English only reads English, so it was tested on the 39 English sentences.

### The confidence check

Every answer from the model comes with a confidence between 0 (a pure guess) and 1 (certain). When
any answer for a sentence is below the **confidence check** (the threshold in Home Assistant), the
sentence is handed to Home Assistant's own assistant instead of being acted on.

### How to read the result cells

Each cell reads **right/total · handed to Home Assistant · wrong**. For example, `48/65 · 4 · **13**`
means 48 sentences were right, 4 were handed to Home Assistant and 13 were wrong. 🔒 counts wrong
sentences in which the front door lock or the garage door would have done the wrong thing. None
occur in the current results. A sentence counts as right only when every device, action and value
in it is right.

## Results

### All 65 sentences at each confidence check

| Model | check 0.0 | check 0.1 | check 0.2 | check 0.3 | check 0.4 | check 0.5 | Median |
|---|---:|---:|---:|---:|---:|---:|---:|
| Laya multilingual | 48/65 · 4 · **13** | 48/65 · 4 · **13** | 48/65 · 5 · **12** | 47/65 · 6 · **12** | 47/65 · 8 · **10** | 43/65 · 13 · **9** | 39 ms |
| Laya English | 33/39 · 2 · **4** | 32/39 · 3 · **4** | 32/39 · 4 · **3** | 31/39 · 5 · **3** | 31/39 · 5 · **3** | 30/39 · 8 · **1** | 67 ms |
| Intern-Decision 0.8B | 54/65 · 3 · **8** | 52/65 · 7 · **6** | 48/65 · 14 · **3** | 39/65 · 25 · **1** | 34/65 · 30 · **1** | 26/65 · 38 · **1** | 549 ms |
| Intern-Decision 2B | 55/65 · 3 · **7** | 55/65 · 3 · **7** | 54/65 · 6 · **5** | 54/65 · 7 · **4** | 54/65 · 9 · **2** | 53/65 · 11 · **1** | 832 ms |
| **d1-3B** | **56/65 · 3 · 6** | 55/65 · 4 · **6** | 55/65 · 4 · **6** | 55/65 · 4 · **6** | 55/65 · 5 · **5** | 54/65 · 7 · **4** | 331 ms |
| d1-omni-600M | 49/65 · 7 · **9** | 47/65 · 9 · **9** | 45/65 · 12 · **8** | 43/65 · 16 · **6** | 42/65 · 19 · **4** | 40/65 · 21 · **4** | 51 ms |

### The original 55 and the 10 new sentences (no confidence check)

| Model | 55 original | 10 new |
|---|---:|---:|
| Laya multilingual | 46/55 · 2 · **7** | 2/10 · 2 · **6** |
| Laya English | 32/34 · 1 · **1** | 1/5 · 1 · **3** |
| Intern-Decision 0.8B | 53/55 · 1 · **1** | 1/10 · 2 · **7** |
| Intern-Decision 2B | 53/55 · 1 · **1** | 2/10 · 2 · **6** |
| **d1-3B** | **54/55 · 1 · 0** | 2/10 · 2 · **6** |
| d1-omni-600M | 47/55 · 3 · **5** | 2/10 · 4 · **4** |

The four models measured before (Laya, Intern-Decision) score exactly as on 2026-10-07 on the 55.

### Memory and speed

| Model | Parameters | Weights | Memory in use (graphics) | Peak RAM while loading | Per question |
|---|---:|---:|---:|---:|---:|
| Laya multilingual | 322 M | 1.2 GB | 1.2 GB | 2.5 GB | 13 ms |
| Laya English | 421 M | 1.6 GB | 2.0 GB | 2.7 GB | 22 ms |
| d1-omni-600M (text part) | 381 M | 0.7 GB | 1.0 GB | 3.3 GB | 15 ms |
| Intern-Decision 0.8B | 853 M | 1.6 GB | 2.3 GB | 0.7 GB | 209 ms |
| Intern-Decision 2B | 2213 M | 4.1 GB | 4.6 GB | 3.0 GB | 276 ms |
| d1-3B | 3123 M | 5.8 GB | 6.8 GB | 0.6 GB | 112 ms |

d1-omni-600M also has vision and audio parts (587 M parameters in all). The server never loads
them. d1-3B's vision tower is loaded with it (about 0.4 B parameters) but never used.

### The new sentences

| Sentence | What happens (all models) |
|---|---|
| "can you turn on the light please" / "kannst du bitte das Licht einschalten" | Right with every model, except that Intern-Decision 0.8B takes the German one for a question. |
| "turn on all the lights in the living room" / "schalte alle Lichter im Wohnzimmer ein" | **Wrong: one light is switched on.** A room still means one device. |
| "turn off the left light but turn on the right one" / "mach das linke Licht aus, aber das rechte an" | **Wrong: only the left light is switched.** "the right one" isn't a name. In German, "linke Licht" doesn't match the alias "Linkes Licht", so the satellite's room is used and some models switch the wrong light. |
| "turn on all the lights on the floor" / "schalte alle Lichter auf dieser Etage ein" | **Wrong: one light in the speaker's room.** Floors aren't targets. |
| "close all the covers on the second floor" / "schließ alle Rollläden im Obergeschoss" | Handed off (`no_target`), which is the safe result. |

To get these right, the pipeline needs "all" over a room or a floor, floor names as targets, and
"the other one" references. See PLAN.md, milestone 2.

### Every mistake on the 55 original sentences (no confidence check)

**Laya multilingual**

- "open the blinds to 30 percent": wanted living_room_blinds:HassSetPosition position=30, got handed off (no_target)
- "dim the desk lamp to 20 and close the garage door": wanted desk_lamp:HassLightSet brightness=20, garage_door:HassTurnOff, got desk_lamp:HassTurnOff, garage_door:HassTurnOff
- "set the thermostat to 22 degrees and the bathroom heating to 24": wanted living_room:HassClimateSetTemperature temperature=22, bathroom:HassClimateSetTemperature temperature=24, got living_room:HassClimateSetTemperature temperature=22, bathroom:HassClimateSetTemperature temperature=22
- "open the blinds to 30 percent" (speaker in living_room): wanted living_room_blinds:HassSetPosition position=30, got living_room_blinds:HassTurnOn
- "dim the light to 30 percent" (speaker in living_room): wanted living_room_floor:HassLightSet brightness=30, got handed off (missing_value)
- "turn on the music" (speaker in living_room): wanted tv:HassTurnOn, got living_room_blinds:HassTurnOn
- "fahr die Rollos auf 30 Prozent" (speaker in living_room): wanted living_room_blinds:HassSetPosition position=30, got living_room_floor:HassTurnOn
- "Licht aus" (speaker in bedroom): wanted desk_lamp:HassTurnOff, got desk_lamp:HassGetState
- "mach es heller, 70 Prozent" (speaker in living_room): wanted living_room_floor:HassLightSet brightness=70, got living_room_floor:HassTurnOn

**Laya English**

- "open the blinds to 30 percent": wanted living_room_blinds:HassSetPosition position=30, got handed off (no_target)
- "open the blinds to 30 percent" (speaker in living_room): wanted living_room_blinds:HassSetPosition position=30, got living_room_floor:HassLightSet brightness=30

**Intern-Decision 0.8B**

- "open the blinds to 30 percent": wanted living_room_blinds:HassSetPosition position=30, got handed off (no_target)
- "mach es heller, 70 Prozent" (speaker in living_room): wanted living_room_floor:HassLightSet brightness=70, got living_room_floor:HassGetState

**Intern-Decision 2B**

- "open the blinds to 30 percent": wanted living_room_blinds:HassSetPosition position=30, got handed off (no_target)
- "mach es heller, 70 Prozent" (speaker in living_room): wanted living_room_floor:HassLightSet brightness=70, got living_room_blinds:HassSetPosition position=70

**d1-3B**

- "open the blinds to 30 percent": wanted living_room_blinds:HassSetPosition position=30, got handed off (no_target)

**d1-omni-600M**

- "open the blinds to 30 percent": wanted living_room_blinds:HassSetPosition position=30, got handed off (no_target)
- "set the thermostat to 22 degrees and the bathroom heating to 24": wanted living_room:HassClimateSetTemperature temperature=22, bathroom:HassClimateSetTemperature temperature=24, got living_room:HassClimateSetTemperature temperature=24, bathroom:HassClimateSetTemperature temperature=24
- "stell die Heizung Wohnzimmer auf 23 Grad und mach das Küchenlicht an": wanted living_room:HassClimateSetTemperature temperature=23, kitchen_ceiling:HassTurnOn, got living_room:HassTurnOn, kitchen_ceiling:HassTurnOn
- "schließ das Garagentor": wanted garage_door:HassTurnOff, got handed off (missing_value)
- "stell die Heizung Bad auf 22 Grad und schalte die Kaffeemaschine ein": wanted bathroom:HassClimateSetTemperature temperature=22, coffee_maker:HassTurnOn, got bathroom:HassTurnOn, coffee_maker:HassTurnOn
- "make it 23 degrees in here" (speaker in living_room): wanted living_room:HassClimateSetTemperature temperature=23, got living_room:HassClimateGetTemperature
- "Licht aus" (speaker in bedroom): wanted desk_lamp:HassTurnOff, got desk_lamp:HassGetState
- "schließ die Rollläden" (speaker in living_room): wanted living_room_blinds:HassTurnOff, got handed off (missing_value)

What they have in common:

1. **"Open the blinds to 30 percent" with no room known is handed off on purpose** by every model.
   Nothing in it names a device or a room, and the test gives no speaker's room.
2. **"Mach es heller, 70 Prozent"** ("make it brighter, 70 percent") fails with Laya and both
   Intern-Decision models. d1-3B and d1-omni-600M get it right.
3. **d1-omni-600M often picks "turn on" for a German "stell … auf N Grad"**, and picks a
   position for "schließ …", which the server then hands off because no number was said.

## What this means for the GeForce plan

| | Fits a 4 GB card? | Right (of 55), no check | Suggested check | At that check (all 65) |
|---|---|---|---|---|
| d1-3B | No, about 6.8 GB | 54 | 0.2 | 55 right, 4 handed off, 6 wrong (all among the new sentences) |
| Intern-Decision 2B | No, about 4.6 GB | 53 | 0.2 | 54 right, 6 handed off, 5 wrong |
| **Intern-Decision 0.8B** | **Yes, about 2.3 GB** | **53** | **0.2** | **48 right, 14 handed off, 3 wrong** |
| d1-omni-600M | Yes, about 1.0 GB | 47 | 0.4 | 42 right, 19 handed off, 4 wrong |
| Laya multilingual | Yes, about 1.2 GB | 46 | 0.4 | 47 right, 8 handed off, 10 wrong |

**Intern-Decision 0.8B is still the pick for the 4 GB card. d1-3B is the pick for any machine with
8 GB or more** (a Mac, or a larger graphics card).

On the Mac, the Qwen-based Intern-Decision models take 200–280 ms per question because two speed-up
libraries (`flash-linear-attention`, `causal-conv1d`) only exist for NVIDIA cards. d1-3B is built
on Liquid AI's LFM2.5, which runs well on the Mac (112 ms per question). Liquid AI reports 8 ms per
question for d1-3B on an RTX 4090. None of this has been measured on NVIDIA here.

## The best approach in the server


The server does this now, in `server/assist_decider_server/pipeline.py`. Step by step:

1. **Find the devices in code, not with the model.** Match the device and room names (and
   aliases) from Home Assistant against the sentence (`find_mentions()`). A named
   device is a target. A named room is a target that the model narrows down in step 3.
   When nothing is named, use the devices of the previous command ("turn it off", only within
   the follow-up memory and when no kind of device such as "light" is said), else the speaker's
   room. When there is no speaker's room either, hand the sentence to Home Assistant.
2. **Ask once per sentence: "Is the user giving a command or asking a question?"** Keep this as its
   own question. Without it, the models answer "is the front door locked" with "lock" and
   "is the light on" with "turn on".
3. **For a named room, ask "Which device does the user mean?"** over that room's devices. Skip the
   room when one of its devices is already named. Locks and garage doors are never offered.
   - ***fit***: first drop the devices that can't take any number in the sentence. A device fits
     when one of its "set" actions accepts a spoken number: brightness 0–100 (% or a bare number),
     blinds position 0–100 (% or a bare number), temperature 5–35 (degrees or a bare number). When
     no device fits, keep them all. When only one is left, don't ask.
4. **For each device, ask "What does the user want with the <device>?"** Offer only the actions its
   kind supports. When step 2 said "question", only "query" is allowed; when it said "command",
   "query" is not. Then the on/off words remove the opposite action:
   - **Locks use their own words** (`lock_words`, `unlock_words` in `lang.py`): "lock", "close",
     "ab", "zu" mean lock; "unlock", "open", "auf", "öffne" mean unlock. "Sperr" and "schließ" decide
     nothing on their own, because "sperr ab" locks and "sperr auf" unlocks.
   - ***near***: when the sentence has both an "on" and an "off" word, each device follows the one
     nearest to its name (counted in words, either side). A tie keeps both. Only when one kind of
     word is said does it apply to every device, as before.
5. **For an action with a value, ask which number from the sentence is meant**, with "no value" as
   an option. The server then checks that the number fits the action (brightness and position
   0–100, temperature 5–35); if not, or if no number was said, it hands the sentence off.
6. **Hand the sentence to Home Assistant when any answer is unsure.** Take the lowest confidence of
   all answers in the sentence. Set the check per model: about **0.2** for Intern-Decision 0.8B and
   2B and d1-3B, 0.4 for d1-omni-600M. The default of 0.4 hands off far too much with the
   Intern models.

Why these two rules and not others: *near* fixed the two sentences that turn one thing on and
another off ("turn on the kitchen light and turn off the hallway light", "Küchenlicht an und den
Fernseher aus") for Intern-Decision 0.8B and Laya multilingual. Over the six models tested then, it
turned 4 wrong sentences right and no right sentence wrong. *fit* stopped
Intern-Decision 2B from setting the heating to 70 degrees. Its answer is still wrong (the blinds),
but now with low confidence, so a check of 0.2 hands it off. Both rules cost no extra model
questions, so the best approach is as fast as the new approach.


## How the best approach was chosen (2026-10-07)

These tables compared four ways of understanding a sentence on the 55 original sentences. They were
measured with a probe script that was removed once the best approach was built into the server.
The script is in the git history (commit `9849dde`, `server/tests/eval/probe_device_tree.py`). The
d1 models came later and are not in these tables.

| Name in the tables | What it does |
|---|---|
| **Today's pipeline** | The code before 2026-10-07. Word lists split the sentence at "and"/"und", and remove actions that can't fit. The model then picks the action and the device from what is left. |
| **New approach** | Devices found by your device and room names. The model decides: command or question, which device in a named room, what to do with each device, which number. |
| **Best approach** | The new approach plus *near* and *fit* (above). This is what the server does now. |
| **Model-only tree** | The model is asked about every device, then the action for each, then the value. No word lists and no name matching. |

#### Whole sentences: today's pipeline against the new approach

Confidence check at **0.0**:

| Model | Today's pipeline | New approach | Best approach |
|---|---:|---:|---:|
| Laya multilingual | 48/55 · 5 · **2** | 45/55 · 1 · **9** | 46/55 · 1 · **8** |
| Laya English | 32/34 · 0 · **2** | 32/34 · 1 · **1** | 32/34 · 1 · **1** |
| Intern-Decision 0.8B | 50/55 · 2 · **3** | 51/55 · 1 · **3** | 53/55 · 1 · **1** |
| Intern-Decision 2B | 50/55 · 3 · **2** | 53/55 · 1 · **1** | 53/55 · 1 · **1** |

Confidence check at **0.4**:

| Model | Today's pipeline | New approach | Best approach |
|---|---:|---:|---:|
| Laya multilingual | 46/55 · 7 · **2** | 43/55 · 5 · **7** | 45/55 · 5 · **5** |
| Laya English | 28/34 · 5 · **1** | 31/34 · 2 · **1** | 31/34 · 3 · **0** |
| Intern-Decision 0.8B | 46/55 · 7 · **2** | 34/55 · 21 · **0** | 37/55 · 18 · **0** |
| Intern-Decision 2B | 49/55 · 4 · **2** | 52/55 · 3 · **0** | 52/55 · 3 · **0** |

#### Best approach by sentence type and language (no confidence check)

| Model | Device named | Only a room / nothing | English | German |
|---|---:|---:|---:|---:|
| Laya multilingual | 29/32 · 1 · **2** | 17/23 · 0 · **6** | 28/34 · 1 · **5** | 18/21 · 0 · **3** |
| Laya English | 19/20 · 1 · **0** | 13/14 · 0 · **1** | 32/34 · 1 · **1** | 0/0 · 0 · **0** |
| Intern-Decision 0.8B | 31/32 · 1 · **0** | 22/23 · 0 · **1** | 33/34 · 1 · **0** | 20/21 · 0 · **1** |
| Intern-Decision 2B | 31/32 · 1 · **0** | 22/23 · 0 · **1** | 33/34 · 1 · **0** | 20/21 · 0 · **1** |

#### Confidence check: what each setting does to the new approach

| Model | check 0.0 | check 0.1 | check 0.2 | check 0.3 | check 0.4 | check 0.5 |
|---|---:|---:|---:|---:|---:|---:|
| Laya multilingual | 45/55 · 1 · **9** | 45/55 · 1 · **9** | 45/55 · 1 · **9** | 44/55 · 3 · **8** | 43/55 · 5 · **7** | 42/55 · 6 · **7** |
| Laya English | 32/34 · 1 · **1** | 32/34 · 1 · **1** | 32/34 · 1 · **1** | 31/34 · 2 · **1** | 31/34 · 2 · **1** | 30/34 · 3 · **1** |
| Intern-Decision 0.8B | 51/55 · 1 · **3** | 49/55 · 3 · **3** | 46/55 · 7 · **2** | 41/55 · 13 · **1** | 34/55 · 21 · **0** | 28/55 · 27 · **0** |
| Intern-Decision 2B | 53/55 · 1 · **1** | 53/55 · 1 · **1** | 53/55 · 1 · **1** | 53/55 · 1 · **1** | 52/55 · 3 · **0** | 50/55 · 5 · **0** |

#### Extra context for the model (no confidence check)

| Model | plain | + devices described | + numbers in options | + model splits sentence | all three |
|---|---:|---:|---:|---:|---:|
| Laya multilingual | 45/55 · 1 · **9** | 40/55 · 1 · **14** | 45/55 · 1 · **9** | 44/55 · 1 · **10** | 40/55 · 1 · **14** (1🔒) |
| Laya English | 32/34 · 1 · **1** | 31/34 · 1 · **2** | 31/34 · 1 · **2** | 32/34 · 1 · **1** | 30/34 · 1 · **3** |
| Intern-Decision 0.8B | 51/55 · 1 · **3** | 51/55 · 1 · **3** | 51/55 · 1 · **3** | 50/55 · 1 · **4** | 49/55 · 1 · **5** |
| Intern-Decision 2B | 53/55 · 1 · **1** | 54/55 · 1 · **0** | 53/55 · 1 · **1** | 52/55 · 1 · **2** | 53/55 · 1 · **1** |

#### Other ways of asking (no confidence check)

| Model | plain | near | fit | near + fit | no command/question step | options asked twice |
|---|---:|---:|---:|---:|---:|---:|
| Laya multilingual | 45/55 · 1 · **9** | 46/55 · 1 · **8** | 45/55 · 1 · **9** | 46/55 · 1 · **8** | 39/55 · 1 · **15** (1🔒) | 45/55 · 1 · **9** |
| Laya English | 32/34 · 1 · **1** | 32/34 · 1 · **1** | 32/34 · 1 · **1** | 32/34 · 1 · **1** | 30/34 · 1 · **3** (1🔒) | 32/34 · 1 · **1** |
| Intern-Decision 0.8B | 51/55 · 1 · **3** | 53/55 · 1 · **1** | 51/55 · 1 · **3** | 53/55 · 1 · **1** | 47/55 · 1 · **7** (1🔒) | 52/55 · 1 · **2** |
| Intern-Decision 2B | 53/55 · 1 · **1** | 53/55 · 1 · **1** | 53/55 · 1 · **1** | 53/55 · 1 · **1** | 52/55 · 1 · **2** | 53/55 · 1 · **1** |

#### The model-only tree, step by step

| Model | Devices, yes ≥ 0.5 | Devices, best cut-off | Devices, top-ranked (count known) | Action per device | Value | Vague change | Whole sentence | Wrong on lock/garage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Laya multilingual | 3/32 | 9/32 (at 0.80) | 21/32 | 35/45 | 9/11 | 1/5 | 2/32 | 12 |
| Laya English | 2/20 | 5/20 (at 0.60) | 13/20 | 26/29 | 7/7 | 2/3 | 2/20 | 7 |
| Intern-Decision 0.8B | 5/32 | 20/32 (at 0.60) | 28/32 | 38/45 | 11/11 | 1/5 | 3/32 | 7 |
| Intern-Decision 2B | 26/32 | 28/32 (at 0.60) | 30/32 | 42/45 | 11/11 | 4/5 | 23/32 | 2 |

#### Time per sentence (median)

| Model | Today's pipeline | New approach | Best approach | Options asked twice |
|---|---:|---:|---:|---:|
| Laya multilingual | 16 ms | 37 ms | 36 ms | 62 ms |
| Laya English | 31 ms | 63 ms | 63 ms | 118 ms |
| Intern-Decision 0.8B | 267 ms | 538 ms | 537 ms | 912 ms |
| Intern-Decision 2B | 351 ms | 715 ms | 712 ms | 1211 ms |

## The lock fix

Locks turn the on/off words around. "Close" and "ab"/"zu" are "off" words for a light, but for a
lock they mean *lock*, which Home Assistant does with "turn on". "Open" and "auf" are "on" words,
but for a lock they mean *unlock*. So before the fix, "sperr die Haustür ab", "schließ die Haustür
ab", "öffne die Haustür" and "open the front door" all did the opposite of what was said.
Now, for a lock, the server uses `lock_words` and `unlock_words` instead of the on/off words
(`server/assist_decider_server/lang.py`, used in `Decider.polarity()` in `pipeline.py`). A test
covers all four sentences.

## Limits of this benchmark

- **65 sentences and one test home.** A difference of one or two sentences between models is noise.
- **The suggested checks were picked on these same sentences.** Confirm them on your own commands
  with the live log.
- **The d1 providers rebuild the models' text path in the server's own code**, from the
  checkpoints' `prompt.py`, `runner.py` and `encoder.py`. They were not compared number by number
  with Liquid AI's own code.
- **Time and memory were measured on the Mac only.** "Memory in use" is what the Apple graphics
  driver reports. "Peak RAM while loading" is the most memory the process used.

## The models

| Model | Made by | Built on | License |
|---|---|---|---|
| [Laya multilingual](https://huggingface.co/convaiinnovations/laya-multilingual) | ConvAI Innovations | mmBERT-base | Apache-2.0 |
| [Laya English](https://huggingface.co/convaiinnovations/laya) | ConvAI Innovations | ModernBERT-large | Apache-2.0 |
| [Intern-Decision 0.8B](https://huggingface.co/internlm/Intern-Decision-0.8B) | InternLM | Qwen3.5-0.8B | Apache-2.0 |
| [Intern-Decision 2B](https://huggingface.co/internlm/Intern-Decision-2B) | InternLM | Qwen3.5-2B | Apache-2.0 |
| [d1-3B](https://huggingface.co/LiquidAI/d1-3B) | Liquid AI | LFM2.5-VL-3B | LFM Open License v1.0 |
| [d1-omni-600M](https://huggingface.co/LiquidAI/d1-omni-600M) | Liquid AI | LFM2.5-Encoder-350M + decision head | LFM Open License v1.0 |

Every download is pinned to a reviewed commit (`server/assist_decider_server/providers.py`).
For Intern-Decision and d1, the server runs no code from the download. It rebuilds the prompt (and
for d1-omni-600M the network, in `d1_omni.py`) itself. The LFM Open License is free to use unless
your organization makes $10M or more a year.

## Repeat the benchmark

From the `server` folder:

```bash
# right / handed off / wrong at every check, and every mistake (big models: a few minutes each)
uv run python tests/eval/benchmark.py multilingual english \
    intern-decision-0.8b intern-decision-2b d1-3b d1-omni-600m

# memory and speed, one process per model
for m in multilingual english intern-decision-0.8b intern-decision-2b d1-3b d1-omni-600m; do
    uv run python tests/eval/measure_memory.py $m
done
```

To switch the server to another model, set `model = "d1-3b"` in the config file, or start it with
`--model d1-3b`.
