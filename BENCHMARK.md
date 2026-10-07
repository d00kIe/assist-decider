# Decision model benchmark

Which small decision model understands home commands best, and what does it cost to run?
This page compares six models that can run locally, on 55 test sentences, in English and German.

Measured on 2026-10-07 (best approach and lock fix added the same day) on a MacBook Pro M4 Pro (Apple graphics, 48 GB), with `laya 0.3.26`,
`transformers 5.18.0` and `torch 2.14.1`. How to repeat it is at the [end](#repeat-the-benchmark).

## Short answer

- **There is now a best approach**: the new approach plus two small rules in code, *near* and
  *fit* (explained [below](#the-best-approach-step-by-step)). It never does worse than the new
  approach, and it helps the small models most.
- **Intern-Decision 0.8B is now as good as the big models**: 53 of 55 right with the best
  approach, the same as Intern-Decision 2B and one less than H2O-Lightning 4B. It needs only
  2.3 GB, so it **fits a 4 GB graphics card**. With a confidence check of 0.2 it makes no
  mistakes at all (49 right, 6 handed to Home Assistant).
- **Most accurate: H2O-Lightning 4B** (54 of 55, no mistakes; the one sentence it misses is handed
  to Home Assistant on purpose). But it needs about 8.8 GB and about 1.1 s per sentence on the Mac,
  so it is **far too big for a 4 GB graphics card**.
- **Intern-Decision 2B** gets 53 of 55, and none wrong at a check of 0.2. It needs 4.6 GB, which is
  **too much for a 4 GB card**.
- **The German lock bug is fixed** in the server. "Sperr die Haustür ab" used to unlock the door
  with every model. Now no model and no approach in the recommended setup does anything wrong to
  the lock or the garage door.
- **Laya English** is still very good (32 of 34) and by far the fastest. **Laya multilingual** still
  makes the most mistakes (8 of 55), mostly in sentences that name only a room.
- **Two ideas did not help**: dropping the "command or question?" step (four models then *lock*
  the door when asked "is the front door locked"), and asking every question twice with the
  options reversed (at most one sentence better, twice as slow).
- **Giving the model more context does not help**, as before: describing devices, putting
  numbers into the options or letting the model split the sentence.
- **Asking the model about every device and then every action only works with the two biggest
  models**: H2O-Lightning 4B (27 of 32 whole sentences) and Intern-Decision 2B (23 of 32).

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

### The four ways of understanding a sentence

| Name in the tables | What it does |
|---|---|
| **Today's pipeline** | The code as it is now in the server. Word lists split the sentence at "and"/"und", and remove actions that can't fit (for example "turn on" when "off" was said). The model then picks the action and the device from what is left. |
| **New approach** | Devices are found by matching your own device and room names from Home Assistant. When nothing is named, the speaker's room is used. The model then decides: (1) is it a command or a question, (2) which device in a named room is meant, (3) what should happen to each device, choosing only from actions that kind of device supports, and (4) which number from the sentence is the value. The on/off words are still used as a safety filter. |
| **Best approach** | The new approach plus two rules in code: *near* (when "on" and "off" are both said, each device follows the word closest to its name) and *fit* (in a room, devices that can't take the spoken number are not offered). Described step by step [below](#the-best-approach-step-by-step). |
| **Model-only tree** | The model is asked about every device ("does the user mean the kitchen light?"), then about the action for each device, then about the value. No word lists and no name matching. |

Since the first version of this page, the server's German and English word lists treat locks
differently (see [the lock fix](#the-lock-fix)). That fix is in every column, including today's
pipeline, so a few numbers are one better than in the first version.

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
| H2O-Lightning 4B | 4539 M | 8.5 GB | 8.8 GB | 0.5 GB | 373 ms |

### Whole sentences: today's pipeline against the new approach

Confidence check at **0.0**:

| Model | Today's pipeline | New approach | Best approach |
|---|---:|---:|---:|
| Laya multilingual | 48/55 · 5 · **2** | 45/55 · 1 · **9** | 46/55 · 1 · **8** |
| Laya English | 32/34 · 0 · **2** | 32/34 · 1 · **1** | 32/34 · 1 · **1** |
| Kev 0.8B | 44/55 · 8 · **3** | 49/55 · 1 · **5** | 50/55 · 1 · **4** |
| Intern-Decision 0.8B | 50/55 · 2 · **3** | 51/55 · 1 · **3** | 53/55 · 1 · **1** |
| Intern-Decision 2B | 50/55 · 3 · **2** | 53/55 · 1 · **1** | 53/55 · 1 · **1** |
| H2O-Lightning 4B | 52/55 · 1 · **2** | 54/55 · 1 · **0** | 54/55 · 1 · **0** |

Confidence check at **0.4**:

| Model | Today's pipeline | New approach | Best approach |
|---|---:|---:|---:|
| Laya multilingual | 46/55 · 7 · **2** | 43/55 · 5 · **7** | 45/55 · 5 · **5** |
| Laya English | 28/34 · 5 · **1** | 31/34 · 2 · **1** | 31/34 · 3 · **0** |
| Kev 0.8B | 39/55 · 14 · **2** | 37/55 · 16 · **2** | 39/55 · 14 · **2** |
| Intern-Decision 0.8B | 46/55 · 7 · **2** | 34/55 · 21 · **0** | 37/55 · 18 · **0** |
| Intern-Decision 2B | 49/55 · 4 · **2** | 52/55 · 3 · **0** | 52/55 · 3 · **0** |
| H2O-Lightning 4B | 51/55 · 2 · **2** | 53/55 · 2 · **0** | 53/55 · 2 · **0** |

### Best approach by sentence type and language (no confidence check)

| Model | Device named | Only a room / nothing | English | German |
|---|---:|---:|---:|---:|
| Laya multilingual | 29/32 · 1 · **2** | 17/23 · 0 · **6** | 28/34 · 1 · **5** | 18/21 · 0 · **3** |
| Laya English | 19/20 · 1 · **0** | 13/14 · 0 · **1** | 32/34 · 1 · **1** | 0/0 · 0 · **0** |
| Kev 0.8B | 30/32 · 1 · **1** | 20/23 · 0 · **3** | 31/34 · 1 · **2** | 19/21 · 0 · **2** |
| Intern-Decision 0.8B | 31/32 · 1 · **0** | 22/23 · 0 · **1** | 33/34 · 1 · **0** | 20/21 · 0 · **1** |
| Intern-Decision 2B | 31/32 · 1 · **0** | 22/23 · 0 · **1** | 33/34 · 1 · **0** | 20/21 · 0 · **1** |
| H2O-Lightning 4B | 31/32 · 1 · **0** | 23/23 · 0 · **0** | 33/34 · 1 · **0** | 21/21 · 0 · **0** |

### Confidence check: what each setting does to the new approach

| Model | check 0.0 | check 0.1 | check 0.2 | check 0.3 | check 0.4 | check 0.5 |
|---|---:|---:|---:|---:|---:|---:|
| Laya multilingual | 45/55 · 1 · **9** | 45/55 · 1 · **9** | 45/55 · 1 · **9** | 44/55 · 3 · **8** | 43/55 · 5 · **7** | 42/55 · 6 · **7** |
| Laya English | 32/34 · 1 · **1** | 32/34 · 1 · **1** | 32/34 · 1 · **1** | 31/34 · 2 · **1** | 31/34 · 2 · **1** | 30/34 · 3 · **1** |
| Kev 0.8B | 49/55 · 1 · **5** | 46/55 · 5 · **4** | 42/55 · 9 · **4** | 39/55 · 14 · **2** | 37/55 · 16 · **2** | 32/55 · 22 · **1** |
| Intern-Decision 0.8B | 51/55 · 1 · **3** | 49/55 · 3 · **3** | 46/55 · 7 · **2** | 41/55 · 13 · **1** | 34/55 · 21 · **0** | 28/55 · 27 · **0** |
| Intern-Decision 2B | 53/55 · 1 · **1** | 53/55 · 1 · **1** | 53/55 · 1 · **1** | 53/55 · 1 · **1** | 52/55 · 3 · **0** | 50/55 · 5 · **0** |
| H2O-Lightning 4B | 54/55 · 1 · **0** | 54/55 · 1 · **0** | 54/55 · 1 · **0** | 53/55 · 2 · **0** | 53/55 · 2 · **0** | 53/55 · 2 · **0** |

### Confidence check: what each setting does to the best approach

| Model | check 0.0 | check 0.1 | check 0.2 | check 0.3 | check 0.4 | check 0.5 |
|---|---:|---:|---:|---:|---:|---:|
| Laya multilingual | 46/55 · 1 · **8** | 46/55 · 1 · **8** | 46/55 · 2 · **7** | 45/55 · 3 · **7** | 45/55 · 5 · **5** | 44/55 · 6 · **5** |
| Laya English | 32/34 · 1 · **1** | 32/34 · 1 · **1** | 32/34 · 2 · **0** | 31/34 · 3 · **0** | 31/34 · 3 · **0** | 30/34 · 4 · **0** |
| Kev 0.8B | 50/55 · 1 · **4** | 47/55 · 5 · **3** | 43/55 · 9 · **3** | 41/55 · 12 · **2** | 39/55 · 14 · **2** | 34/55 · 20 · **1** |
| Intern-Decision 0.8B | 53/55 · 1 · **1** | 51/55 · 3 · **1** | 49/55 · 6 · **0** | 44/55 · 11 · **0** | 37/55 · 18 · **0** | 31/55 · 24 · **0** |
| Intern-Decision 2B | 53/55 · 1 · **1** | 53/55 · 1 · **1** | 52/55 · 3 · **0** | 52/55 · 3 · **0** | 52/55 · 3 · **0** | 50/55 · 5 · **0** |
| H2O-Lightning 4B | 54/55 · 1 · **0** | 54/55 · 1 · **0** | 53/55 · 2 · **0** | 53/55 · 2 · **0** | 53/55 · 2 · **0** | 53/55 · 2 · **0** |

### Extra context for the model (no confidence check)

| Model | plain | + devices described | + numbers in options | + model splits sentence | all three |
|---|---:|---:|---:|---:|---:|
| Laya multilingual | 45/55 · 1 · **9** | 40/55 · 1 · **14** | 45/55 · 1 · **9** | 44/55 · 1 · **10** | 40/55 · 1 · **14** (1🔒) |
| Laya English | 32/34 · 1 · **1** | 31/34 · 1 · **2** | 31/34 · 1 · **2** | 32/34 · 1 · **1** | 30/34 · 1 · **3** |
| Kev 0.8B | 49/55 · 1 · **5** | 47/55 · 1 · **7** | 49/55 · 1 · **5** | 45/55 · 1 · **9** | 45/55 · 1 · **9** |
| Intern-Decision 0.8B | 51/55 · 1 · **3** | 51/55 · 1 · **3** | 51/55 · 1 · **3** | 50/55 · 1 · **4** | 49/55 · 1 · **5** |
| Intern-Decision 2B | 53/55 · 1 · **1** | 54/55 · 1 · **0** | 53/55 · 1 · **1** | 52/55 · 1 · **2** | 53/55 · 1 · **1** |
| H2O-Lightning 4B | 54/55 · 1 · **0** | 54/55 · 1 · **0** | 54/55 · 1 · **0** | 54/55 · 1 · **0** | 54/55 · 1 · **0** |

### Other ways of asking (no confidence check)

| Model | plain | near | fit | near + fit | no command/question step | options asked twice |
|---|---:|---:|---:|---:|---:|---:|
| Laya multilingual | 45/55 · 1 · **9** | 46/55 · 1 · **8** | 45/55 · 1 · **9** | 46/55 · 1 · **8** | 39/55 · 1 · **15** (1🔒) | 45/55 · 1 · **9** |
| Laya English | 32/34 · 1 · **1** | 32/34 · 1 · **1** | 32/34 · 1 · **1** | 32/34 · 1 · **1** | 30/34 · 1 · **3** (1🔒) | 32/34 · 1 · **1** |
| Kev 0.8B | 49/55 · 1 · **5** | 50/55 · 1 · **4** | 49/55 · 1 · **5** | 50/55 · 1 · **4** | 47/55 · 1 · **7** (1🔒) | 46/55 · 1 · **8** |
| Intern-Decision 0.8B | 51/55 · 1 · **3** | 53/55 · 1 · **1** | 51/55 · 1 · **3** | 53/55 · 1 · **1** | 47/55 · 1 · **7** (1🔒) | 52/55 · 1 · **2** |
| Intern-Decision 2B | 53/55 · 1 · **1** | 53/55 · 1 · **1** | 53/55 · 1 · **1** | 53/55 · 1 · **1** | 52/55 · 1 · **2** | 53/55 · 1 · **1** |
| H2O-Lightning 4B | 54/55 · 1 · **0** | 54/55 · 1 · **0** | 54/55 · 1 · **0** | 54/55 · 1 · **0** | 54/55 · 1 · **0** | 54/55 · 1 · **0** |

### The model-only tree, step by step

| Model | Devices, yes ≥ 0.5 | Devices, best cut-off | Devices, top-ranked (count known) | Action per device | Value | Vague change | Whole sentence | Wrong on lock/garage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Laya multilingual | 3/32 | 9/32 (at 0.80) | 21/32 | 35/45 | 9/11 | 1/5 | 2/32 | 12 |
| Laya English | 2/20 | 5/20 (at 0.60) | 13/20 | 26/29 | 7/7 | 2/3 | 2/20 | 7 |
| Kev 0.8B | 12/32 | 20/32 (at 0.65) | 29/32 | 38/45 | 10/11 | 4/5 | 8/32 | 3 |
| Intern-Decision 0.8B | 5/32 | 20/32 (at 0.60) | 28/32 | 38/45 | 11/11 | 1/5 | 3/32 | 7 |
| Intern-Decision 2B | 26/32 | 28/32 (at 0.60) | 30/32 | 42/45 | 11/11 | 4/5 | 23/32 | 2 |
| H2O-Lightning 4B | 28/32 | 28/32 (at 0.30) | 32/32 | 44/45 | 11/11 | 4/5 | 27/32 | 2 |

### Time per sentence (median)

| Model | Today's pipeline | New approach | Best approach | Options asked twice |
|---|---:|---:|---:|---:|
| Laya multilingual | 16 ms | 37 ms | 36 ms | 62 ms |
| Laya English | 31 ms | 63 ms | 63 ms | 118 ms |
| Kev 0.8B | 154 ms | 218 ms | 183 ms | 373 ms |
| Intern-Decision 0.8B | 267 ms | 538 ms | 537 ms | 912 ms |
| Intern-Decision 2B | 351 ms | 715 ms | 712 ms | 1211 ms |
| H2O-Lightning 4B | 693 ms | 1098 ms | 1094 ms | 2096 ms |

### Every mistake of the best approach (no confidence check)

**Laya multilingual** (9)

- "open the blinds to 30 percent": wanted living_room_blinds:set_position=30, got handed to Home Assistant
- "dim the desk lamp to 20 and close the garage door": wanted desk_lamp:set_brightness=20, garage_door:close, got desk_lamp:turn_off, garage_door:close
- "set the thermostat to 22 degrees and the bathroom heating to 24": wanted living_room:set_temperature=22, bathroom:set_temperature=24, got living_room:set_temperature=22, bathroom:set_temperature=22
- "open the blinds to 30 percent" (speaker in living_room): wanted living_room_blinds:set_position=30, got living_room_blinds:open
- "dim the light to 30 percent" (speaker in living_room): wanted living_room_floor:set_brightness=30, got living_room_floor:set_brightness
- "turn on the music" (speaker in living_room): wanted tv:turn_on, got living_room_blinds:open
- "fahr die Rollos auf 30 Prozent" (speaker in living_room): wanted living_room_blinds:set_position=30, got living_room_floor:turn_on
- "Licht aus" (speaker in bedroom): wanted desk_lamp:turn_off, got desk_lamp:query
- "mach es heller, 70 Prozent" (speaker in living_room): wanted living_room_floor:set_brightness=70, got living_room_floor:turn_on

**Laya English** (2)

- "open the blinds to 30 percent": wanted living_room_blinds:set_position=30, got handed to Home Assistant
- "open the blinds to 30 percent" (speaker in living_room): wanted living_room_blinds:set_position=30, got living_room_floor:set_brightness=30

**Kev 0.8B** (5)

- "open the blinds to 30 percent": wanted living_room_blinds:set_position=30, got handed to Home Assistant
- "set the thermostat to 22 degrees and the bathroom heating to 24": wanted living_room:set_temperature=22, bathroom:set_temperature=24, got living_room:set_temperature=24, bathroom:set_temperature=24
- "make it 23 degrees in here" (speaker in living_room): wanted living_room:set_temperature=23, got living_room:query
- "Licht aus" (speaker in bedroom): wanted desk_lamp:turn_off, got desk_lamp:query
- "mach es heller, 70 Prozent" (speaker in living_room): wanted living_room_floor:set_brightness=70, got living_room_floor:query

**Intern-Decision 0.8B** (2)

- "open the blinds to 30 percent": wanted living_room_blinds:set_position=30, got handed to Home Assistant
- "mach es heller, 70 Prozent" (speaker in living_room): wanted living_room_floor:set_brightness=70, got living_room_floor:query

**Intern-Decision 2B** (2)

- "open the blinds to 30 percent": wanted living_room_blinds:set_position=30, got handed to Home Assistant
- "mach es heller, 70 Prozent" (speaker in living_room): wanted living_room_floor:set_brightness=70, got living_room_blinds:set_position=70

**H2O-Lightning 4B** (1)

- "open the blinds to 30 percent": wanted living_room_blinds:set_position=30, got handed to Home Assistant


## The best approach, step by step

This is the recipe to build into the server later. The probe's version is `_mix()` with the
context `("near", "fit")` in `server/tests/eval/probe_device_tree.py`, together with its helpers
`polarity()` and `fits()`.

1. **Find the devices in code, not with the model.** Match the device and room names (and
   aliases) from Home Assistant against the sentence, as `find_mentions()` already does. A named
   device is a target. A named room is a target that the model narrows down in step 3.
   When nothing is named, use the speaker's room. When there is no speaker's room either, hand the
   sentence to Home Assistant.
2. **Ask once per sentence: "Is the user giving a command or asking a question?"** Keep this as its
   own question. Without it, the models answer "is the front door locked" with "lock" and
   "is the light on" with "turn on".
3. **For a named room, ask "Which device does the user mean?"** over that room's devices. Skip the
   room when one of its devices is already named.
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
   an option.
6. **Hand the sentence to Home Assistant when any answer is unsure.** Take the lowest confidence of
   all answers in the sentence. Set the check per model: about **0.2** for Intern-Decision 0.8B and
   2B, 0.0–0.1 for H2O-Lightning 4B. The server default of 0.4 hands off far too much with the
   Intern models.

Why these two rules and not others: *near* fixed the two sentences that turn one thing on and
another off ("turn on the kitchen light and turn off the hallway light", "Küchenlicht an und den
Fernseher aus") for Intern-Decision 0.8B, Kev and Laya multilingual. Over all six models it
turned 4 wrong sentences right and no right sentence wrong. *fit* stopped
Intern-Decision 2B from setting the heating to 70 degrees. Its answer is still wrong (the blinds),
but now with low confidence, so a check of 0.2 hands it off. Both rules cost no extra model
questions, so the best approach is as fast as the new approach.

## The lock fix

Locks turn the on/off words around. "Close" and "ab"/"zu" are "off" words for a light, but for a
lock they mean *lock*, which Home Assistant does with "turn on". "Open" and "auf" are "on" words,
but for a lock they mean *unlock*. So before the fix, "sperr die Haustür ab", "schließ die Haustür
ab", "öffne die Haustür" and "open the front door" all did the opposite of what was said.
Now, when a sentence part names a lock, the server uses `lock_words` and `unlock_words` instead
of the on/off words (`server/assist_decider_server/lang.py`, used in `candidate_intents()` in
`pipeline.py`). A test covers all four sentences.

## What the mistakes have in common

1. **"Open the blinds to 30 percent" with no room known is handed to Home Assistant on purpose.**
   Nothing in it matches a device or room name, and the test gives no speaker's room.
2. **"Mach es heller, 70 Prozent"** ("make it brighter, 70 percent") still fails with every model
   that reads German except H2O-Lightning 4B. Intern-Decision 0.8B and Kev take it for a question;
   Intern-Decision 2B now moves the blinds to 70 instead of the heating. "heller" is already in the
   German value words and could point to a light, but a rule made for one test sentence would
   only fit this page, so it is not part of the best approach.
3. **Laya multilingual and Kev** still mix up some sentences that name only a room, and the second
   number in "set the thermostat to 22 degrees and the bathroom heating to 24". The bigger models
   don't.

## What this means for the GeForce plan

| | Fits a 4 GB card? | Best approach, right (of 55), no check | Suggested check | At that check |
|---|---|---|---|---|
| H2O-Lightning 4B | No, about 8.8 GB | 54 | 0.0–0.1 | 54 right, 1 handed off, 0 wrong |
| Intern-Decision 2B | No, unless compressed to 8-bit (untested) | 53 | 0.2 | 52 right, 3 handed off, 0 wrong |
| **Intern-Decision 0.8B** | **Yes, about 2.3 GB** | **53** | **0.2** | **49 right, 6 handed off, 0 wrong** |
| Kev 0.8B | Yes, about 2.3 GB | 50 | 0.4 | 39 right, 14 handed off, 2 wrong |
| Laya multilingual | Yes, about 1.2 GB | 46 | 0.4 | 45 right, 5 handed off, 5 wrong |

**Intern-Decision 0.8B with the best approach is the pick for the 4 GB card.**

On the Mac, the Qwen-based models (Kev, Intern-Decision and H2O-Lightning) take 60 to 370 ms per question. Two
speed-up libraries (`flash-linear-attention`, `causal-conv1d`) only exist for NVIDIA cards, so on
the Mac they run slow fallback code. On an NVIDIA card they should be much faster. Intern-Decision's
model card reports 34 ms per question on an RTX 4090, and H2O-Lightning's reports 34 ms on a 32 GB
RTX PRO 4500. Neither has been measured here.

## Limits of this benchmark

- **55 sentences and one test home.** A difference of one or two sentences between models is noise.
- **The confidence settings in the tables were tried on these same sentences.** A setting that looks
  best here needs confirming on new sentences before it goes into the configuration. The same goes
  for *near* and *fit*: they are general rules, not fitted to single sentences, but they were
  found while looking at these mistakes.
- **Kev 0.8B was trained on English only, and H2O-Lightning 4B was evaluated mostly in English.**
  Their German results are measured here, but their makers don't claim German support.
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
| [H2O-Lightning 4B](https://huggingface.co/h2oai/h2o-lightning-4b) | H2O.ai | Qwen3.5-4B | Apache-2.0 |

Every download is pinned to a reviewed commit (`server/assist_decider_server/providers.py`). For Kev,
Intern-Decision and H2O-Lightning, the server does not run any code from the download. It rebuilds the
prompt format itself. The Kev version gives the same probabilities as Kev's own code to within 0.003.
H2O-Lightning normally runs on vLLM behind a small server of its own. The provider here rebuilds that
server's prompt in `transformers` instead, and it reproduces the model card's example answer to
within 0.006.

## Repeat the benchmark

From the `server` folder:

```bash
# answers for every sentence, saved to tests/eval/results/<model>.json (slow models: 5-15 minutes)
uv run python tests/eval/probe_device_tree.py multilingual english kev-0.8b \
    intern-decision-0.8b intern-decision-2b h2o-lightning-4b --tree --out=tests/eval/results

# memory and speed, one process per model
for m in multilingual english kev-0.8b intern-decision-0.8b intern-decision-2b h2o-lightning-4b; do
    uv run python tests/eval/measure_memory.py $m --out=tests/eval/results
done

# the tables on this page
uv run python tests/eval/report.py
```

To switch the server to another model, set `model = "intern-decision-2b"` in the config file, or
start it with `--model intern-decision-2b`.
