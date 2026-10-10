# Decision model benchmark

How well each of the six decision models understands home commands with the current server, and how
that compares with the previous server version. The test uses 106 sentences in English and German.

- **Previous version:** commit `1fd399e` (2026-10-10).
- **Current version:** the server as it is in this repository (2026-10-10).

Both versions were run on 2026-10-10 on the same machine: a MacBook Pro M4 Pro with Apple graphics
and 48 GB of memory, `laya 0.3.26`, `transformers 5.18.0` and `torch 2.14.1`. Both used the same
script (`server/tests/eval/benchmark.py`), which sends every sentence through the server's own code.
How to repeat it is at the [end](#repeat-the-benchmark).

## Short answer

- **Most models got better.** On the 41 unseen sentences, d1-3B, Intern-Decision 0.8B and Laya
  multilingual now get more sentences right and fewer wrong. Intern-Decision 2B and Laya English
  get one more right.
  Only d1-omni-600M got worse: two more sentences wrong.
- **The biggest gain is on sentences that are not about the home**, such as "tell me a joke" or
  "set a timer for ten minutes". More of them now go back to Home Assistant instead of switching a
  device. Intern-Decision 0.8B now gets 3 of these 5 right instead of 1.
- **Every model is faster**, because the model now answers two questions fewer per sentence.
  d1-3B: 0.67 s → 0.50 s per sentence. Intern-Decision 0.8B: 0.59 s → 0.49 s.
- **The best model is still d1-3B:** on the tuning sentences 64 right, 1 handed off, 0 wrong. On
  the unseen sentences 35 right, 2 handed off, 4 wrong.
- **For a 4 GB graphics card, Intern-Decision 0.8B with a check of 0.3 is still the pick.** On the
  unseen sentences it went from 23 right · 4 wrong to 25 right · 3 wrong. On the tuning
  sentences it now gets 3 fewer right but also 2 fewer wrong.
- **"If …" sentences are no longer supported, and no longer tested.** "If it gets dark, turn on
  the floor lamp" turns the lamp on right away.
- **One mistake on a lock is still there:** d1-3B locks the front door when told "sperr die
  Haustür auf" ("unlock the front door"), with a confidence of 0.90.

## What changed between the two versions

| | Previous version | Current version |
|---|---|---|
| "If …" sentences | The model was asked two extra questions to find them, and they were passed on to Home Assistant. | Not supported. The action runs right away. These sentences were removed from the test. |
| "Is this about the home?" | The answer offered to the model was a fixed list: "lights, heating, blinds, locks, the TV, music, plugs or other devices at home". | The list is made from the devices in your home. For the test home: "lights, switches, blinds, heating, TV, locks or other devices at home". |
| Questions about every sentence | Four. | Two: is it a command or a question, and is it about the home. |

How the current version works in detail is in [HOW-IT-WORKS.md](HOW-IT-WORKS.md).

## How it was measured

### The sentences

- **65 tuning sentences.** The server's questions were adjusted while looking at the results on
  these, so they flatter the current version. They have three groups:
  - 32 name a device ("turn on the kitchen light and turn off the hallway light").
  - 23 name only a room or nothing, said to a voice satellite in a known room ("Licht aus" in the
    bedroom).
  - 10 are polite, or about "all", floors, or left and right.
- **41 unseen sentences.** They were written before the current version was built and were never
  used to adjust it. They cover vague requests, three devices in one sentence, sentences not
  about the home, "all" and floors, follow-ups, the lock and garage door, German short forms
  ("Rollos runter") and left/right. 10 of them must be handed off. One
  exception: they exposed two code bugs, which were fixed ("make" read as another ending of
  "Coffee Maker", and "zweiten" not matching "zweiter"). So the current version's unseen numbers
  are slightly optimistic.

Laya English only reads English, so it was tested on the 39 English tuning sentences and the 19
English unseen sentences.

### The test home

The first 55 tuning sentences use the home of the automatic tests (`server/tests/conftest.py`). It
has 5 rooms and 11 devices: 4 lights, 2 heating thermostats, living room blinds, a garage door, a
front door lock, a coffee maker and a TV. German names come from each device's alias, such as
"Küchenlicht". The other sentences use the same home with additions (`HOME_FLOORS` in
`benchmark.py`): two floors, an office and a kids' room upstairs with blinds each, and a left and a
right light in the living room.

### Right, handed off, wrong

- **Right:** every device, action and value is right, and nothing else is touched. For a sentence
  that must be handed off (a joke, "I'm freezing"), handing it off is right.
- **Handed off:** the server doesn't act and passes the sentence on to Home Assistant's own
  assistant (or another fallback). Safe, but nothing happens.
- **Wrong:** the server would have done something that wasn't asked for.

Each result cell reads **right · handed off · wrong**. For example, `49 · 12 · **4**` means 49 right,
12 handed off and 4 wrong. 🔒 counts the wrong sentences where the front door lock or the garage
door would have done the wrong thing.

### The confidence check

Every answer from the model comes with a confidence between 0 (a pure guess) and 1 (certain). A
sentence's confidence is the lowest confidence among the answers the server used. When it is below
the **check** (the confidence threshold you set in Home Assistant), the sentence is handed off.

In the current version, changing a lock or garage door with a confidence below 0.5 gets a
confidence of 0.00. So at check 0.0 it still runs, and at any check above 0 it is handed off.

The **suggested check** is the one, of 0.0 to 0.5, with the best result on the tuning sentences,
where one wrong sentence costs as much as three right ones. A tie goes to the higher check.
"Most right with none wrong" is the most tuning sentences right at any check where none is wrong.

## Summary

| Model | Version | Tuning (65), check 0.0 | Unseen (41), check 0.0 | Suggested check | Tuning, at that check | Unseen, at that check | Most right with none wrong (tuning) | Median time per sentence |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| d1-3B | previous | 63 · 2 · **0** | 34 · 2 · **5** (1 🔒) | 0.0 | 63 · 2 · **0** | 34 · 2 · **5** (1 🔒) | 63 | 665 ms |
|  | **current** | 64 · 1 · **0** | 35 · 2 · **4** (1 🔒) | 0.0 | 64 · 1 · **0** | 35 · 2 · **4** (1 🔒) | 64 | 500 ms |
| Intern-Decision 2B | previous | 60 · 2 · **3** | 33 · 4 · **4** (1 🔒) | 0.0 | 60 · 2 · **3** | 33 · 4 · **4** (1 🔒) | 20 | 742 ms |
|  | **current** | 60 · 2 · **3** | 33 · 4 · **4** (1 🔒) | 0.2 | 58 · 5 · **2** | 35 · 5 · **1** | 16 | 636 ms |
| Intern-Decision 0.8B | previous | 55 · 1 · **9** | 23 · 2 · **16** (2 🔒) | 0.3 | 49 · 12 · **4** | 23 · 14 · **4** | 29 | 593 ms |
|  | **current** | 55 · 2 · **8** | 27 · 2 · **12** (2 🔒) | 0.3 | 46 · 17 · **2** | 25 · 13 · **3** | 31 | 485 ms |
| d1-omni-600M | previous | 45 · 16 · **4** | 28 · 6 · **7** | 0.3 | 37 · 27 · **1** | 23 · 15 · **3** | 30 | 116 ms |
|  | **current** | 48 · 13 · **4** | 27 · 5 · **9** | 0.3 | 40 · 24 · **1** | 22 · 14 · **5** | 33 | 93 ms |
| Laya multilingual | previous | 53 · 0 · **12** | 19 · 1 · **21** (2 🔒) | 0.5 | 40 · 19 · **6** | 16 · 18 · **7** | 26 | 54 ms |
|  | **current** | 53 · 0 · **12** | 20 · 1 · **20** (2 🔒) | 0.5 | 40 · 19 · **6** | 17 · 18 · **6** | 26 | 45 ms |
| Laya English | previous | 34 · 1 · **4** | 11 · 2 · **6** | 0.0 | 34 · 1 · **4** | 11 · 2 · **6** | 21 | 108 ms |
|  | **current** | 34 · 1 · **4** | 12 · 1 · **6** | 0.0 | 34 · 1 · **4** | 12 · 1 · **6** | 21 | 95 ms |

## Every check: the 65 tuning sentences

| Model | Version | check 0.0 | check 0.1 | check 0.2 | check 0.3 | check 0.4 | check 0.5 |
|---|---|---:|---:|---:|---:|---:|---:|
| d1-3B | previous | 63 · 2 · **0** | 61 · 4 · **0** | 59 · 6 · **0** | 59 · 6 · **0** | 55 · 10 · **0** | 50 · 15 · **0** |
|  | current | 64 · 1 · **0** | 62 · 3 · **0** | 60 · 5 · **0** | 60 · 5 · **0** | 56 · 9 · **0** | 51 · 14 · **0** |
| Intern-Decision 2B | previous | 60 · 2 · **3** | 55 · 7 · **3** | 54 · 8 · **3** | 52 · 11 · **2** | 47 · 16 · **2** | 46 · 17 · **2** |
|  | current | 60 · 2 · **3** | 59 · 3 · **3** | 58 · 5 · **2** | 56 · 7 · **2** | 51 · 12 · **2** | 49 · 14 · **2** |
| Intern-Decision 0.8B | previous | 55 · 1 · **9** | 55 · 1 · **9** | 52 · 6 · **7** | 49 · 12 · **4** | 46 · 15 · **4** | 38 · 25 · **2** |
|  | current | 55 · 2 · **8** | 55 · 2 · **8** | 52 · 8 · **5** | 46 · 17 · **2** | 42 · 21 · **2** | 39 · 24 · **2** |
| d1-omni-600M | previous | 45 · 16 · **4** | 42 · 19 · **4** | 40 · 22 · **3** | 37 · 27 · **1** | 36 · 28 · **1** | 30 · 34 · **1** |
|  | current | 48 · 13 · **4** | 45 · 16 · **4** | 43 · 19 · **3** | 40 · 24 · **1** | 39 · 25 · **1** | 33 · 31 · **1** |
| Laya multilingual | previous | 53 · 0 · **12** | 53 · 0 · **12** | 46 · 8 · **11** | 46 · 10 · **9** | 43 · 14 · **8** | 40 · 19 · **6** |
|  | current | 53 · 0 · **12** | 53 · 0 · **12** | 46 · 8 · **11** | 46 · 10 · **9** | 43 · 14 · **8** | 40 · 19 · **6** |
| Laya English | previous | 34 · 1 · **4** | 33 · 2 · **4** | 30 · 5 · **4** | 27 · 9 · **3** | 27 · 10 · **2** | 25 · 12 · **2** |
|  | current | 34 · 1 · **4** | 33 · 2 · **4** | 30 · 5 · **4** | 27 · 9 · **3** | 27 · 10 · **2** | 25 · 12 · **2** |

## Every check: the 41 unseen sentences

| Model | Version | check 0.0 | check 0.1 | check 0.2 | check 0.3 | check 0.4 | check 0.5 |
|---|---|---:|---:|---:|---:|---:|---:|
| d1-3B | previous | 34 · 2 · **5** (1 🔒) | 33 · 3 · **5** (1 🔒) | 33 · 3 · **5** (1 🔒) | 30 · 6 · **5** (1 🔒) | 28 · 8 · **5** (1 🔒) | 26 · 10 · **5** (1 🔒) |
|  | current | 35 · 2 · **4** (1 🔒) | 34 · 3 · **4** (1 🔒) | 34 · 3 · **4** (1 🔒) | 31 · 6 · **4** (1 🔒) | 29 · 8 · **4** (1 🔒) | 27 · 10 · **4** (1 🔒) |
| Intern-Decision 2B | previous | 33 · 4 · **4** (1 🔒) | 34 · 6 · **1** | 34 · 6 · **1** | 33 · 7 · **1** | 32 · 8 · **1** | 29 · 12 · **0** |
|  | current | 33 · 4 · **4** (1 🔒) | 35 · 5 · **1** | 35 · 5 · **1** | 34 · 6 · **1** | 32 · 9 · **0** | 28 · 13 · **0** |
| Intern-Decision 0.8B | previous | 23 · 2 · **16** (2 🔒) | 23 · 6 · **12** | 23 · 10 · **8** | 23 · 14 · **4** | 20 · 18 · **3** | 16 · 23 · **2** |
|  | current | 27 · 2 · **12** (2 🔒) | 26 · 6 · **9** | 22 · 12 · **7** | 25 · 13 · **3** | 22 · 17 · **2** | 19 · 20 · **2** |
| d1-omni-600M | previous | 28 · 6 · **7** | 25 · 9 · **7** | 24 · 12 · **5** | 23 · 15 · **3** | 19 · 20 · **2** | 15 · 24 · **2** |
|  | current | 27 · 5 · **9** | 24 · 8 · **9** | 23 · 11 · **7** | 22 · 14 · **5** | 18 · 19 · **4** | 14 · 23 · **4** |
| Laya multilingual | previous | 19 · 1 · **21** (2 🔒) | 20 · 4 · **17** | 19 · 7 · **15** | 16 · 15 · **10** | 15 · 17 · **9** | 16 · 18 · **7** |
|  | current | 20 · 1 · **20** (2 🔒) | 21 · 4 · **16** | 20 · 7 · **14** | 17 · 15 · **9** | 16 · 17 · **8** | 17 · 18 · **6** |
| Laya English | previous | 11 · 2 · **6** | 12 · 4 · **3** | 13 · 4 · **2** | 13 · 4 · **2** | 13 · 4 · **2** | 12 · 6 · **1** |
|  | current | 12 · 1 · **6** | 13 · 3 · **3** | 14 · 3 · **2** | 14 · 3 · **2** | 14 · 3 · **2** | 13 · 5 · **1** |

## By kind of sentence (right, check 0.0)

| Kind of sentence | Sentences | d1-3B | Intern 2B | Intern 0.8B | d1-omni | Laya multi | Laya EN |
|---|---:|---:|---:|---:|---:|---:|---:|
| Tuning: A device is named | 32 | 32 → **32** | 32 → **32** | 29 → **30** | 27 → **29** | 28 → **28** | 19 → **19** (of 20) |
| Tuning: Only a room or nothing is named | 23 | 21 → **22** | 21 → **21** | 20 → **20** | 12 → **13** | 20 → **20** | 12 → **12** (of 14) |
| Tuning: Polite, all, floors, left / right | 10 | 10 → **10** | 7 → **7** | 6 → **5** | 6 → **6** | 5 → **5** | 3 → **3** (of 5) |
| Unseen: Vague requests | 5 | 3 → **3** | 2 → **2** | 1 → **1** | 3 → **3** | 1 → **1** | 1 → **1** (of 3) |
| Unseen: Three devices in one sentence | 6 | 6 → **6** | 6 → **6** | 3 → **4** | 4 → **4** | 3 → **3** | 3 → **3** (of 3) |
| Unseen: Not about the home (must be handed off) | 5 | 4 → **5** | 4 → **4** | 1 → **3** | 5 → **3** | 1 → **2** | 0 → **0** (of 3) |
| Unseen: All, floors, the whole home | 7 | 6 → **6** | 6 → **6** | 4 → **5** | 5 → **5** | 2 → **2** | 3 → **3** (of 4) |
| Unseen: Follow-ups | 4 | 4 → **4** | 4 → **4** | 4 → **4** | 3 → **3** | 3 → **3** | 1 → **2** (of 2) |
| Unseen: Front door lock and garage door | 6 | 4 → **4** | 4 → **4** | 3 → **3** | 5 → **6** | 3 → **3** | 2 → **2** (of 3) |
| Unseen: German short forms | 5 | 4 → **4** | 4 → **4** | 4 → **4** | 1 → **1** | 4 → **4** | 0 → **0** (of 0) |
| Unseen: Left / right | 3 | 3 → **3** | 3 → **3** | 3 → **3** | 2 → **2** | 2 → **2** | 1 → **1** (of 1) |

## Memory and speed

| Model | Parameters | Weights | Memory in use (graphics) | Peak RAM while loading | Per model call |
|---|---:|---:|---:|---:|---:|
| d1-3B | 3123 M | 5.8 GB | 6.8 GB | 0.7 GB | 83 ms |
| Intern-Decision 2B | 2213 M | 4.1 GB | 4.6 GB | 0.7 GB | 273 ms |
| Intern-Decision 0.8B | 853 M | 1.6 GB | 2.3 GB | 0.7 GB | 208 ms |
| d1-omni-600M | 381 M | 0.7 GB | 1.0 GB | 3.3 GB | 15 ms |
| Laya multilingual | 322 M | 1.2 GB | 1.2 GB | 2.5 GB | 13 ms |
| Laya English | 421 M | 1.6 GB | 2.0 GB | 2.7 GB | 23 ms |

"Per model call" is the median time for one call with one question. The current version puts
several questions in one call, so the time per sentence (in the summary) is the number to compare.
d1-omni-600M also has vision and audio parts, which the server never loads. d1-3B's vision part
(about 0.4 B parameters) is loaded but never used. On the Mac, Intern-Decision runs without two
speed-up libraries that only exist for NVIDIA cards. Nothing has been measured on NVIDIA yet.

## What the current version still gets wrong

- **German "… aus" at the end of a one-device sentence** ("mach das Licht im Flur aus", "Licht im
  Flur aus") is often read as "on". Intern-Decision 0.8B gets both wrong; d1-3B, d1-omni-600M
  and Laya multilingual get one of the two wrong.
- **Sentences not about the home** ("play some jazz", "set a timer for ten minutes") still switch a
  device in the speaker's room with some models, mostly with low confidence.
- **"Turn off all the lights in the house"** typed without a speaker's room is handed off by every
  model: the "where?" question is only asked when the speaker's room is known.
- **"All the lights on this floor"** without the floor's name is done in the speaker's room only,
  by every model except d1-3B (and d1-omni-600M in English): the server widens to the floor only
  when the model is very sure (0.9).
- **"Open the door"** said in the hallway turns on the hallway light with every model except
  d1-omni-600M, instead of being handed off.

## Every sentence

Each cell shows the previous version's result, an arrow, and the current version's result with its
confidence, all at check 0.0:

- ✓ right · ✗ wrong · ✗🔒 wrong on the lock or garage door
- ↪ handed off, but it should have been done · ✓↪ handed off, as it should be
- The number is the sentence's confidence. With a check above it, the sentence would be handed
  off instead. 0.00 on a lock or garage door means the 0.5 lock rule hands it off at any check
  above 0.
- · not tested (Laya English doesn't read German)


### Tuning: A device is named

| Sentence | d1-3B | Intern 2B | Intern 0.8B | d1-omni | Laya multi | Laya EN |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| "set the thermostat to 23 degrees and turn on the light in the kitchen" | ✓ → ✓ 0.51 | ✓ → ✓ 0.40 | ✓ → ✓ 0.46 | ✓ → ✓ 0.95 | ✓ → ✓ 0.57 | ✓ → ✓ 0.47 |
| "turn on the kitchen light" | ✓ → ✓ 0.98 | ✓ → ✓ 0.84 | ✓ → ✓ 0.61 | ✓ → ✓ 0.99 | ✓ → ✓ 1.00 | ✓ → ✓ 0.96 |
| "turn off the hallway light" | ✓ → ✓ 0.98 | ✓ → ✓ 0.91 | ✓ → ✓ 0.90 | ✓ → ✓ 0.98 | ✓ → ✓ 1.00 | ✓ → ✓ 0.98 |
| "turn on the kitchen light and turn off the hallway light" | ✓ → ✓ 0.95 | ✓ → ✓ 0.76 | ✗ → ✓ 0.28 | ✓ → ✓ 0.65 | ✓ → ✓ 0.73 | ✓ → ✓ 0.88 |
| "turn off the kitchen and hallway lights" | ✓ → ✓ 0.42 | ✓ → ✓ 0.55 | ✓ → ✓ 0.24 | ✓ → ✓ 0.62 | ✓ → ✓ 0.19 | ✓ → ✓ 0.29 |
| "set the kitchen light to 40 percent" | ✓ → ✓ 0.98 | ✓ → ✓ 0.90 | ✓ → ✓ 0.86 | ✓ → ✓ 0.96 | ✓ → ✓ 0.98 | ✓ → ✓ 0.96 |
| "set the bathroom heating to 21 degrees" | ✓ → ✓ 0.97 | ✓ → ✓ 0.92 | ✓ → ✓ 0.87 | ✓ → ✓ 0.99 | ✓ → ✓ 1.00 | ✓ → ✓ 0.95 |
| "close the living room blinds" | ✓ → ✓ 0.98 | ✓ → ✓ 0.89 | ✓ → ✓ 0.80 | ✓ → ✓ 0.84 | ✓ → ✓ 1.00 | ✓ → ✓ 0.98 |
| "open the blinds to 30 percent" | ✓ → ✓ 0.97 | ✓ → ✓ 0.83 | ✓ → ✓ 0.78 | ✓ → ✓ 0.46 | ✗ → ✗ 0.98 | ✗ → ✗ 0.78 |
| "lock the front door" | ✓ → ✓ 0.98 | ✓ → ✓ 0.88 | ✓ → ✓ 0.73 | ✓ → ✓ 0.83 | ✓ → ✓ 1.00 | ✓ → ✓ 0.95 |
| "unlock the front door" | ✓ → ✓ 0.96 | ✓ → ✓ 0.90 | ✓ → ✓ 0.83 | ✓ → ✓ 0.97 | ✓ → ✓ 1.00 | ✓ → ✓ 0.95 |
| "start the coffee maker and turn off the tv" | ✓ → ✓ 0.97 | ✓ → ✓ 0.81 | ✓ → ✓ 0.36 | ✓ → ✓ 0.01 | ✓ → ✓ 0.82 | ✓ → ✓ 0.79 |
| "is the kitchen light on" | ✓ → ✓ 0.87 | ✓ → ✓ 0.83 | ✓ → ✓ 0.53 | ↪ → ✓ 0.98 | ✓ → ✓ 0.99 | ✓ → ✓ 0.95 |
| "what's the temperature in the bathroom" | ✓ → ✓ 0.92 | ✓ → ✓ 0.78 | ✓ → ✓ 0.77 | ↪ → ↪ | ✓ → ✓ 0.95 | ✓ → ✓ 0.58 |
| "dim the desk lamp to 20 and close the garage door" | ✓ → ✓ 0.96 | ✓ → ✓ 0.88 | ✓ → ✓ 0.69 | ✓ → ✓ 0.63 | ✓ → ✓ 1.00 | ✓ → ✓ 0.98 |
| "switch off the tv, the floor lamp and the hallway light" | ✓ → ✓ 0.42 | ✓ → ✓ 0.34 | ✓ → ✓ 0.42 | ✓ → ✓ 0.52 | ✓ → ✓ 0.30 | ✓ → ✓ 0.47 |
| "set the thermostat to 22 degrees and the bathroom heating to 24" | ✓ → ✓ 0.75 | ✓ → ✓ 0.89 | ✓ → ✓ 0.73 | ↪ → ✓ 0.96 | ✓ → ✓ 1.00 | ✓ → ✓ 0.86 |
| "turn on the coffee maker" | ✓ → ✓ 0.98 | ✓ → ✓ 0.91 | ✓ → ✓ 0.78 | ✓ → ✓ 0.98 | ✓ → ✓ 1.00 | ✓ → ✓ 0.97 |
| "is the front door locked" | ✓ → ✓ 0.90 | ✓ → ✓ 0.79 | ✓ → ✓ 0.31 | ✓ → ✓ 0.98 | ✓ → ✓ 0.99 | ✓ → ✓ 0.93 |
| "turn the floor lamp off and open the garage door" | ✓ → ✓ 0.97 | ✓ → ✓ 0.89 | ✓ → ✓ 0.74 | ✓ → ✓ 1.00 | ✓ → ✓ 1.00 | ✓ → ✓ 0.93 |
| "schalte das Küchenlicht ein" | ✓ → ✓ 0.97 | ✓ → ✓ 0.79 | ✓ → ✓ 0.72 | ✓ → ✓ 0.80 | ✓ → ✓ 0.99 | · |
| "mach das Licht im Flur aus" | ✓ → ✓ 0.97 | ✓ → ✓ 0.56 | ✗ → ✗ 0.30 | ✓ → ✓ 0.14 | ✗ → ✗ 0.92 | · |
| "stell die Heizung Wohnzimmer auf 23 Grad und mach das Küchenlicht an" | ✓ → ✓ 0.95 | ✓ → ✓ 0.80 | ✓ → ✓ 0.56 | ✓ → ✓ 0.36 | ✓ → ✓ 0.96 | · |
| "mach das Küchenlicht an und den Fernseher aus" | ✓ → ✓ 0.90 | ✓ → ✓ 0.69 | ✓ → ✓ 0.12 | ✓ → ✓ 0.55 | ✗ → ✗ 0.14 | · |
| "fahr den Rollladen Wohnzimmer auf 30 Prozent" | ✓ → ✓ 0.97 | ✓ → ✓ 0.83 | ✓ → ✓ 0.83 | ✓ → ✓ 0.52 | ✓ → ✓ 0.92 | · |
| "schließ das Garagentor" | ✓ → ✓ 0.70 | ✓ → ✓ 0.82 | ✓ → ✓ 0.69 | ↪ → ↪ | ✓ → ✓ 0.99 | · |
| "sperr die Haustür ab" | ✓ → ✓ 0.95 | ✓ → ✓ 0.57 | ✓ → ✓ 0.89 | ✓ → ✓ 0.00 | ✓ → ✓ 0.87 | · |
| "ist das Küchenlicht an" | ✓ → ✓ 0.46 | ✓ → ✓ 0.12 | ✓ → ✓ 0.59 | ✓ → ✓ 0.55 | ✓ → ✓ 0.94 | · |
| "dimme die Schreibtischlampe auf 20 Prozent" | ✓ → ✓ 0.98 | ✓ → ✓ 0.85 | ✓ → ✓ 0.84 | ✓ → ✓ 0.94 | ✓ → ✓ 0.99 | · |
| "stell die Heizung Bad auf 22 Grad und schalte die Kaffeemaschine ein" | ✓ → ✓ 0.97 | ✓ → ✓ 0.90 | ✓ → ✓ 0.31 | ✓ → ✓ 0.59 | ✓ → ✓ 0.99 | · |
| "wie warm ist es im Bad" | ✓ → ✓ 0.95 | ✓ → ✓ 0.80 | ✓ → ✓ 0.90 | ↪ → ↪ | ✓ → ✓ 1.00 | · |
| "mach die Stehlampe und das Küchenlicht aus" | ✓ → ✓ 0.30 | ✓ → ✓ 0.61 | ✗ → ✗ 0.19 | ✓ → ✓ 0.28 | ✗ → ✗ 0.28 | · |

### Tuning: Only a room or nothing is named

| Sentence | d1-3B | Intern 2B | Intern 0.8B | d1-omni | Laya multi | Laya EN |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| "turn on the light" *(in kitchen)* | ✓ → ✓ 0.41 | ✓ → ✓ 0.39 | ✓ → ✓ 0.27 | ✓ → ✓ 0.04 | ✓ → ✓ 0.47 | ✓ → ✓ 0.18 |
| "open the blinds to 30 percent" *(in living room)* | ✓ → ✓ 0.97 | ✓ → ✓ 0.83 | ✓ → ✓ 0.78 | ✓ → ✓ 0.46 | ✗ → ✗ 0.98 | ✗ → ✗ 0.78 |
| "set the temperature to 22 degrees" *(in bathroom)* | ✓ → ✓ 0.98 | ✓ → ✓ 0.81 | ✓ → ✓ 0.84 | ↪ → ✓ 0.79 | ✓ → ✓ 0.99 | ✓ → ✓ 0.96 |
| "make it 23 degrees in here" *(in living room)* | ✓ → ✓ 0.97 | ✓ → ✓ 0.78 | ✓ → ✓ 0.74 | ↪ → ↪ | ✓ → ✓ 0.99 | ✓ → ✓ 0.30 |
| "turn off the lights" *(in bedroom)* | ✓ → ✓ 0.98 | ✓ → ✓ 0.78 | ✓ → ✓ 0.80 | ✓ → ✓ 0.76 | ✓ → ✓ 1.00 | ✓ → ✓ 0.95 |
| "close the blinds" *(in living room)* | ✓ → ✓ 0.98 | ✓ → ✓ 0.89 | ✓ → ✓ 0.79 | ✓ → ✓ 0.56 | ✓ → ✓ 0.99 | ✓ → ✓ 0.97 |
| "dim the light to 30 percent" *(in living room)* | ✓ → ✓ 0.12 | ✓ → ✓ 0.29 | ✓ → ✓ 0.11 | ✓ → ✓ 0.45 | ✓ → ✓ 0.45 | ✓ → ✓ 0.10 |
| "turn on the coffee" *(in kitchen)* | ✓ → ✓ 0.98 | ✓ → ✓ 0.91 | ✓ → ✓ 0.67 | ↪ → ↪ | ✓ → ✓ 0.99 | ✓ → ✓ 0.97 |
| "switch off the light in the bedroom" *(in kitchen)* | ✓ → ✓ 0.98 | ✓ → ✓ 0.81 | ✓ → ✓ 0.87 | ✓ → ✓ 1.00 | ✓ → ✓ 0.99 | ✓ → ✓ 0.96 |
| "turn off the kitchen and hallway lights" | ✓ → ✓ 0.42 | ✓ → ✓ 0.55 | ✓ → ✓ 0.24 | ✓ → ✓ 0.62 | ✓ → ✓ 0.19 | ✓ → ✓ 0.29 |
| "what's the temperature in here" *(in bathroom)* | ↪ → ✓ 0.89 | ✓ → ✓ 0.70 | ✓ → ✓ 0.77 | ↪ → ↪ | ✓ → ✓ 0.89 | ✓ → ✓ 0.77 |
| "is the light on" *(in kitchen)* | ✓ → ✓ 0.12 | ↪ → ✓ 0.32 | ✓ → ✓ 0.29 | ↪ → ↪ | ✓ → ✓ 0.30 | ↪ → ↪ |
| "turn off the tv" *(in kitchen)* | ✓ → ✓ 0.98 | ✓ → ✓ 0.92 | ✓ → ✓ 0.91 | ✓ → ✓ 0.91 | ✓ → ✓ 1.00 | ✓ → ✓ 0.98 |
| "turn on the music" *(in living room)* | ✓ → ✓ 0.70 | ✓ → ✓ 0.67 | ✓ → ✓ 0.61 | ↪ → ↪ | ✓ → ✓ 0.13 | ✓ → ✓ 0.17 |
| "mach das Licht an" *(in kitchen)* | ✓ → ✓ 0.06 | ✓ → ✓ 0.34 | ✓ → ✓ 0.16 | ✓ → ✓ 0.27 | ✓ → ✓ 0.55 | · |
| "fahr die Rollos auf 30 Prozent" *(in living room)* | ✓ → ✓ 0.70 | ↪ → ↪ | ✓ → ✓ 0.55 | ✓ → ✓ 0.15 | ✓ → ✓ 0.32 | · |
| "stell die Heizung auf 22 Grad" *(in bathroom)* | ✓ → ✓ 0.97 | ✓ → ✓ 0.79 | ✓ → ✓ 0.70 | ✓ → ✓ 0.94 | ✓ → ✓ 0.99 | · |
| "Licht aus" *(in bedroom)* | ✓ → ✓ 0.97 | ✓ → ✓ 0.62 | ↪ → ↪ | ✗ → ✗ 0.20 | ✗ → ✗ 0.40 | · |
| "schließ die Rollläden" *(in living room)* | ✓ → ✓ 0.36 | ✓ → ↪ | ✓ → ✓ 0.69 | ↪ → ↪ | ✓ → ✓ 0.14 | · |
| "mach die Kaffeemaschine an" *(in hallway)* | ✓ → ✓ 0.98 | ✓ → ✓ 0.42 | ✓ → ✓ 0.87 | ↪ → ↪ | ✓ → ✓ 0.97 | · |
| "mach das Licht im Schlafzimmer aus" *(in kitchen)* | ✓ → ✓ 0.95 | ✓ → ✓ 0.69 | ✗ → ✗ 0.23 | ✓ → ✓ 0.46 | ✗ → ✗ 0.98 | · |
| "wie warm ist es hier" *(in bathroom)* | ↪ → ↪ | ✓ → ✓ 0.86 | ✓ → ✓ 0.90 | ↪ → ↪ | ✓ → ✓ 0.98 | · |
| "mach es heller, 70 Prozent" *(in living room)* | ✓ → ✓ 0.36 | ✓ → ✓ 0.29 | ✗ → ✗ 0.18 | ↪ → ↪ | ✓ → ✓ 0.14 | · |

### Tuning: Polite, all, floors, left / right

| Sentence | d1-3B | Intern 2B | Intern 0.8B | d1-omni | Laya multi | Laya EN |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| "can you turn on the light please" *(in kitchen)* | ✓ → ✓ 0.67 | ✓ → ✓ 0.53 | ✓ → ✓ 0.27 | ✓ → ✓ 0.45 | ✓ → ✓ 0.12 | ✓ → ✓ 0.03 |
| "turn on all the lights in the living room" | ✓ → ✓ 0.83 | ✓ → ✓ 0.08 | ✓ → ✓ 0.50 | ✓ → ✓ 0.93 | ✓ → ✓ 0.42 | ✓ → ✓ 0.54 |
| "turn off the left light but turn on the right one" *(in living room)* | ✓ → ✓ 0.77 | ✓ → ✓ 0.43 | ✗ → ✗ 0.22 | ✗ → ✗ 0.28 | ✓ → ✓ 0.85 | ✗ → ✗ 0.29 |
| "turn on all the lights on the floor" *(in living room)* | ✓ → ✓ 0.73 | ✗ → ✗ 0.73 | ✗ → ✗ 0.63 | ✓ → ✓ 0.82 | ✗ → ✗ 0.22 | ✗ → ✗ 0.38 |
| "close all the covers on the second floor" | ✓ → ✓ 0.69 | ✓ → ✓ 0.51 | ✓ → ↪ | ✓ → ✓ 0.73 | ✗ → ✗ 0.48 | ✓ → ✓ 0.78 |
| "kannst du bitte das Licht einschalten" *(in kitchen)* | ✓ → ✓ 0.06 | ✓ → ✓ 0.75 | ✓ → ✓ 0.41 | ✓ → ✓ 0.25 | ✓ → ✓ 0.19 | · |
| "schalte alle Lichter im Wohnzimmer ein" | ✓ → ✓ 0.64 | ✓ → ✓ 0.50 | ✓ → ✓ 0.58 | ✓ → ✓ 0.42 | ✗ → ✗ 0.38 | · |
| "mach das linke Licht aus, aber das rechte an" *(in living room)* | ✓ → ✓ 0.38 | ✗ → ✗ 0.19 | ✗ → ✗ 0.17 | ✗ → ✗ 0.24 | ✗ → ✗ 0.96 | · |
| "schalte alle Lichter auf dieser Etage ein" *(in living room)* | ✓ → ✓ 0.70 | ✗ → ✗ 0.85 | ✗ → ✗ 0.64 | ✗ → ✗ 0.51 | ✗ → ✗ 0.64 | · |
| "schließ alle Rollläden im Obergeschoss" | ✓ → ✓ 0.86 | ✓ → ✓ 0.78 | ✓ → ✓ 0.39 | ↪ → ↪ | ✓ → ✓ 0.77 | · |

### Unseen: Vague requests

| Sentence | d1-3B | Intern 2B | Intern 0.8B | d1-omni | Laya multi | Laya EN |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| "it's too dark in here" *(in kitchen)* | ✓ → ✓ 0.06 | ↪ → ↪ | ✗ → ✗ 0.14 | ✗ → ✗ 0.25 | ✗ → ✗ 0.26 | ✗ → ✗ 0.03 |
| "hier ist es viel zu dunkel" *(in bedroom)* | ↪ → ↪ | ↪ → ↪ | ✗ → ✗ 0.65 | ✗ → ✗ 0.66 | ✗ → ✗ 0.93 | · |
| "I'm freezing" *(in bathroom)* | ✗ → ✗ 0.56 | ✗ → ✗ 0.00 | ✗ → ✗ 0.39 | ✓↪ → ✓↪ | ✗ → ✗ 0.75 | ✗ → ✗ 0.92 |
| "mir ist zu warm" *(in living room)* | ✓↪ → ✓↪ | ✓↪ → ✓↪ | ✗ → ✗ 0.22 | ✓↪ → ✓↪ | ✗ → ✗ 0.09 | · |
| "make it a bit brighter" *(in bedroom)* | ✓↪ → ✓↪ | ✓↪ → ✓↪ | ✓↪ → ✓↪ | ✓↪ → ✓↪ | ✓↪ → ✓↪ | ✓↪ → ✓↪ |

### Unseen: Three devices in one sentence

| Sentence | d1-3B | Intern 2B | Intern 0.8B | d1-omni | Laya multi | Laya EN |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| "turn off the tv, close the living room blinds and set the thermostat to 21 degrees" | ✓ → ✓ 0.41 | ✓ → ✓ 0.45 | ✓ → ✓ 0.36 | ↪ → ↪ | ✓ → ✓ 0.27 | ✓ → ✓ 0.44 |
| "mach das Küchenlicht an, die Stehlampe aus und stell die Heizung Bad auf 23 Grad" | ✓ → ✓ 0.91 | ✓ → ✓ 0.71 | ✓ → ✓ 0.42 | ✓ → ✓ 0.34 | ✗ → ✗ 0.30 | · |
| "switch on the coffee maker and the kitchen light and turn off the hallway light" | ✓ → ✓ 0.82 | ✓ → ✓ 0.68 | ✗ → ✗ 0.23 | ✓ → ✓ 0.66 | ✓ → ✓ 0.97 | ✓ → ✓ 0.72 |
| "schalte den Fernseher und die Stehlampe aus und öffne den Rollladen Wohnzimmer" | ✓ → ✓ 0.29 | ✓ → ✓ 0.51 | ✓ → ✓ 0.51 | ✗ → ✗ 0.17 | ✗ → ✗ 0.30 | · |
| "dim the floor lamp to 30 percent and set the bathroom heating to 22 degrees" | ✓ → ✓ 0.98 | ✓ → ✓ 0.92 | ↪ → ✓ 0.81 | ✓ → ✓ 0.99 | ✓ → ✓ 1.00 | ✓ → ✓ 0.94 |
| "Schreibtischlampe auf 40 Prozent und Küchenlicht aus" | ✓ → ✓ 0.95 | ✓ → ✓ 0.40 | ✗ → ✗ 0.10 | ✓ → ✓ 0.40 | ✗ → ✗ 0.14 | · |

### Unseen: Not about the home (must be handed off)

| Sentence | d1-3B | Intern 2B | Intern 0.8B | d1-omni | Laya multi | Laya EN |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| "tell me a joke" *(in kitchen)* | ✓↪ → ✓↪ | ✓↪ → ✓↪ | ✗ → ✓↪ | ✓↪ → ✓↪ | ✗ → ✗ 0.14 | ✗ → ✗ 0.18 |
| "wie spät ist es" *(in living room)* | ✓↪ → ✓↪ | ✓↪ → ✓↪ | ✓↪ → ✓↪ | ✓↪ → ✗ 0.57 | ✓↪ → ✓↪ | · |
| "set a timer for ten minutes" *(in kitchen)* | ✓↪ → ✓↪ | ✓↪ → ✓↪ | ✗ → ✓↪ | ✓↪ → ✓↪ | ✗ → ✗ 0.02 | ✗ → ✗ 0.05 |
| "play some jazz" *(in living room)* | ✗ → ✓↪ | ✗ → ✗ 0.09 | ✗ → ✗ 0.24 | ✓↪ → ✓↪ | ✗ → ✗ 0.47 | ✗ → ✗ 0.01 |
| "was ist die Hauptstadt von Frankreich" *(in bedroom)* | ✓↪ → ✓↪ | ✓↪ → ✓↪ | ✗ → ✗ 0.87 | ✓↪ → ✗ 0.89 | ✗ → ✓↪ | · |

### Unseen: All, floors, the whole home

| Sentence | d1-3B | Intern 2B | Intern 0.8B | d1-omni | Laya multi | Laya EN |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| "turn off all the lights in the house" | ↪ → ↪ | ↪ → ↪ | ↪ → ↪ | ↪ → ↪ | ↪ → ↪ | ↪ → ↪ |
| "schalte alle Lichter im Erdgeschoss aus" | ✓ → ✓ 0.64 | ✓ → ✓ 0.81 | ✓ → ✓ 0.37 | ✓ → ✓ 0.27 | ✗ → ✗ 0.69 | · |
| "close the blinds in the office and in the kids room" | ✓ → ✓ 0.52 | ✓ → ✓ 0.38 | ✓ → ✓ 0.34 | ✓ → ✓ 0.36 | ✓ → ✓ 0.35 | ✓ → ✓ 0.42 |
| "fahr alle Rollos im zweiten Stock runter" | ✓ → ✓ 0.50 | ✓ → ✓ 0.73 | ✓ → ✓ 0.18 | ↪ → ↪ | ✗ → ✗ 0.85 | · |
| "turn on the lights" *(in living room)* | ✓ → ✓ 0.24 | ✓ → ✓ 0.34 | ✗ → ✓ 0.14 | ✓ → ✓ 0.45 | ✗ → ✗ 0.22 | ✓ → ✓ 0.07 |
| "mach die Lichter im Wohnzimmer aus" | ✓ → ✓ 0.24 | ✓ → ✓ 0.71 | ✗ → ↪ | ✓ → ✓ 0.20 | ✗ → ✗ 0.27 | · |
| "open all the blinds on the second floor" | ✓ → ✓ 0.91 | ✓ → ✓ 0.59 | ✓ → ✓ 0.18 | ✓ → ✓ 0.89 | ✓ → ✓ 0.14 | ✓ → ✓ 0.75 |

### Unseen: Follow-ups

| Sentence | d1-3B | Intern 2B | Intern 0.8B | d1-omni | Laya multi | Laya EN |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| "turn it off again" *(after "turn on the kitchen light")* | ✓ → ✓ 0.97 | ✓ → ✓ 0.86 | ✓ → ✓ 0.74 | ✓ → ✓ 0.96 | ✓ → ✓ 0.99 | ✓ → ✓ 0.98 |
| "und jetzt wieder aus" *(after "mach die Stehlampe an")* | ✓ → ✓ 0.93 | ✓ → ✓ 0.74 | ✓ → ✓ 0.37 | ✓ → ✓ 0.04 | ✓ → ✓ 0.13 | · |
| "make it 23" *(after "set the bathroom heating to 21 degrees")* | ✓ → ✓ 0.94 | ✓ → ✓ 0.78 | ✓ → ✓ 0.49 | ↪ → ↪ | ✓ → ✓ 0.77 | ↪ → ✓ 0.64 |
| "mach sie auf 50 Prozent" *(after "schalte die Schreibtischlampe ein")* | ✓ → ✓ 0.95 | ✓ → ✓ 0.56 | ✓ → ✓ 0.44 | ✓ → ✓ 0.41 | ✗ → ✗ 0.46 | · |

### Unseen: Front door lock and garage door

| Sentence | d1-3B | Intern 2B | Intern 0.8B | d1-omni | Laya multi | Laya EN |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| "did I lock the front door" | ✓ → ✓ 0.93 | ✓ → ✓ 0.49 | ✗🔒 → ✗🔒 0.00 | ↪ → ✓ 0.96 | ✗🔒 → ✗🔒 0.00 | ✓ → ✓ 0.91 |
| "ist die Haustür zu" | ✓ → ✓ 0.69 | ✗🔒 → ✗🔒 0.00 | ✗🔒 → ✗🔒 0.00 | ✓ → ✓ 0.49 | ✓ → ✓ 0.93 | · |
| "sperr die Haustür auf" | ✗🔒 → ✗🔒 0.90 | ✓ → ✓ 0.63 | ✓ → ✓ 0.00 | ✓ → ✓ 0.00 | ✗🔒 → ✗🔒 0.00 | · |
| "schließ die Haustür ab" | ✓ → ✓ 0.94 | ✓ → ✓ 0.65 | ✓ → ✓ 0.83 | ✓ → ✓ 0.00 | ✓ → ✓ 0.92 | · |
| "open the door" *(in hallway)* | ✗ → ✗ 0.78 | ✗ → ✗ 0.34 | ✗ → ✗ 0.20 | ✓↪ → ✓↪ | ✗ → ✗ 1.00 | ✗ → ✗ 0.45 |
| "close the garage" | ✓↪ → ✓↪ | ✓↪ → ✓↪ | ✓↪ → ✓↪ | ✓↪ → ✓↪ | ✓↪ → ✓↪ | ✓↪ → ✓↪ |

### Unseen: German short forms

| Sentence | d1-3B | Intern 2B | Intern 0.8B | d1-omni | Laya multi | Laya EN |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| "Rollos runter" *(in living room)* | ✓ → ✓ 0.46 | ↪ → ↪ | ✓ → ✓ 0.62 | ✗ → ✗ 0.17 | ✓ → ✓ 0.26 | · |
| "mach die Rollläden im Wohnzimmer auf" | ✓ → ✓ 0.36 | ✓ → ✓ 0.75 | ✓ → ✓ 0.15 | ↪ → ↪ | ✓ → ✓ 0.07 | · |
| "dreh die Heizung im Bad auf 22 Grad" | ✓ → ✓ 0.98 | ✓ → ✓ 0.82 | ✓ → ✓ 0.74 | ✓ → ✓ 0.30 | ✓ → ✓ 0.61 | · |
| "Licht im Flur aus" | ✗ → ✗ 0.76 | ✓ → ✓ 0.44 | ✗ → ✗ 0.16 | ✗ → ✗ 0.31 | ✓ → ✓ 0.21 | · |
| "schalte den Fernseher ab" | ✓ → ✓ 0.98 | ✓ → ✓ 0.86 | ✓ → ✓ 0.89 | ✗ → ✗ 0.68 | ✗ → ✗ 0.98 | · |

### Unseen: Left / right

| Sentence | d1-3B | Intern 2B | Intern 0.8B | d1-omni | Laya multi | Laya EN |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| "mach das rechte Licht an" *(in living room)* | ✓ → ✓ 0.96 | ✓ → ✓ 0.46 | ✓ → ✓ 0.52 | ✗ → ✗ 0.26 | ✓ → ✓ 0.95 | · |
| "turn off the right light" *(in living room)* | ✓ → ✓ 0.98 | ✓ → ✓ 0.90 | ✓ → ✓ 0.89 | ✓ → ✓ 0.93 | ✓ → ✓ 1.00 | ✓ → ✓ 0.71 |
| "schalte das linke Licht und die Stehlampe aus" | ✓ → ✓ 0.36 | ✓ → ✓ 0.27 | ✓ → ✓ 0.56 | ✓ → ✓ 0.31 | ✗ → ✗ 0.26 | · |

## Mistakes of the current version (check 0.0)

What each model did wrong at check 0.0, with the sentence's confidence after the dot. "(in …)" is the speaker's room. Click a model to open its list.

<details><summary>d1-3B: 7</summary>

- "wie warm ist es hier" (in bathroom): wanted Bathroom Heating: ask the temperature; got handed off (not about the home)
- "hier ist es viel zu dunkel" (in bedroom): wanted Desk Lamp: on; got handed off (no value)
- "I'm freezing" (in bathroom): wanted handed off; got Bathroom Heating: ask the temperature · 0.56
- "turn off all the lights in the house": wanted Desk Lamp: off, Hallway Light: off, Kitchen Light: off, Floor Lamp: off, Left Light: off, Right Light: off; got handed off (no device or room found)
- "sperr die Haustür auf": wanted Front Door: unlock; got Front Door: lock · 0.90
- "open the door" (in hallway): wanted handed off; got Hallway Light: on · 0.78
- "Licht im Flur aus": wanted Hallway Light: off; got Hallway Light: on · 0.76

</details>

<details><summary>Intern-Decision 2B: 13</summary>

- "fahr die Rollos auf 30 Prozent" (in living room): wanted Living Room Blinds: to 30 %; got handed off (not about the home)
- "schließ die Rollläden" (in living room): wanted Living Room Blinds: close; got handed off (not about the home)
- "turn on all the lights on the floor" (in living room): wanted Hallway Light: on, Kitchen Light: on, Floor Lamp: on, Left Light: on, Right Light: on; got Floor Lamp: on, Left Light: on, Right Light: on · 0.73
- "mach das linke Licht aus, aber das rechte an" (in living room): wanted Left Light: off, Right Light: on; got Left Light: off, Right Light: off · 0.19
- "schalte alle Lichter auf dieser Etage ein" (in living room): wanted Hallway Light: on, Kitchen Light: on, Floor Lamp: on, Left Light: on, Right Light: on; got Floor Lamp: on, Left Light: on, Right Light: on · 0.85
- "it's too dark in here" (in kitchen): wanted Kitchen Light: on; got handed off (not supported)
- "hier ist es viel zu dunkel" (in bedroom): wanted Desk Lamp: on; got handed off (no value)
- "I'm freezing" (in bathroom): wanted handed off; got Bathroom Heating: off · 0.00
- "play some jazz" (in living room): wanted handed off; got TV: on · 0.09
- "turn off all the lights in the house": wanted Desk Lamp: off, Hallway Light: off, Kitchen Light: off, Floor Lamp: off, Left Light: off, Right Light: off; got handed off (no device or room found)
- "ist die Haustür zu": wanted Front Door: ask how it is; got Front Door: lock · 0.00
- "open the door" (in hallway): wanted handed off; got Hallway Light: on · 0.34
- "Rollos runter" (in living room): wanted Living Room Blinds: close; got handed off (not about the home)

</details>

<details><summary>Intern-Decision 0.8B: 24</summary>

- "mach das Licht im Flur aus": wanted Hallway Light: off; got Hallway Light: on · 0.30
- "mach die Stehlampe und das Küchenlicht aus": wanted Kitchen Light: off, Floor Lamp: off; got Kitchen Light: on, Floor Lamp: off · 0.19
- "Licht aus" (in bedroom): wanted Desk Lamp: off; got handed off (no value)
- "mach das Licht im Schlafzimmer aus" (in kitchen): wanted Desk Lamp: off; got Desk Lamp: on · 0.23
- "mach es heller, 70 Prozent" (in living room): wanted Floor Lamp: to 70 %; got Floor Lamp: ask how it is · 0.18
- "turn off the left light but turn on the right one" (in living room): wanted Left Light: off, Right Light: on; got Left Light: off, Right Light: off · 0.22
- "turn on all the lights on the floor" (in living room): wanted Hallway Light: on, Kitchen Light: on, Floor Lamp: on, Left Light: on, Right Light: on; got Floor Lamp: on, Left Light: on, Right Light: on · 0.63
- "close all the covers on the second floor": wanted Kids Room Blinds: close, Office Blinds: close; got handed off (answers contradict each other)
- "mach das linke Licht aus, aber das rechte an" (in living room): wanted Left Light: off, Right Light: on; got Left Light: off, Right Light: off · 0.17
- "schalte alle Lichter auf dieser Etage ein" (in living room): wanted Hallway Light: on, Kitchen Light: on, Floor Lamp: on, Left Light: on, Right Light: on; got Floor Lamp: on, Left Light: on, Right Light: on · 0.64
- "it's too dark in here" (in kitchen): wanted Kitchen Light: on; got Kitchen Light: ask how it is · 0.14
- "hier ist es viel zu dunkel" (in bedroom): wanted Desk Lamp: on; got Desk Lamp: ask how it is · 0.65
- "I'm freezing" (in bathroom): wanted handed off; got Bathroom Heating: ask the temperature · 0.39
- "mir ist zu warm" (in living room): wanted handed off; got Thermostat: ask the temperature · 0.22
- "switch on the coffee maker and the kitchen light and turn off the hallway light": wanted Hallway Light: off, Kitchen Light: on, Coffee Maker: on; got Hallway Light: off, Kitchen Light: off, Coffee Maker: on · 0.23
- "Schreibtischlampe auf 40 Prozent und Küchenlicht aus": wanted Desk Lamp: to 40 %, Kitchen Light: off; got Desk Lamp: to 40 %, Kitchen Light: to 40 % · 0.10
- "play some jazz" (in living room): wanted handed off; got TV: on · 0.24
- "was ist die Hauptstadt von Frankreich" (in bedroom): wanted handed off; got Desk Lamp: ask how it is · 0.87
- "turn off all the lights in the house": wanted Desk Lamp: off, Hallway Light: off, Kitchen Light: off, Floor Lamp: off, Left Light: off, Right Light: off; got handed off (no device or room found)
- "mach die Lichter im Wohnzimmer aus": wanted Floor Lamp: off, Left Light: off, Right Light: off; got handed off (answers contradict each other)
- "did I lock the front door": wanted Front Door: ask how it is; got Front Door: lock · 0.00
- "ist die Haustür zu": wanted Front Door: ask how it is; got Front Door: unlock · 0.00
- "open the door" (in hallway): wanted handed off; got Hallway Light: on · 0.20
- "Licht im Flur aus": wanted Hallway Light: off; got Hallway Light: on · 0.16

</details>

<details><summary>d1-omni-600M: 31</summary>

- "what's the temperature in the bathroom": wanted Bathroom Heating: ask the temperature; got handed off (not about the home)
- "schließ das Garagentor": wanted Garage Door: close; got handed off (no value)
- "wie warm ist es im Bad": wanted Bathroom Heating: ask the temperature; got handed off (not about the home)
- "make it 23 degrees in here" (in living room): wanted Thermostat: to 23 °; got handed off (not about the home)
- "turn on the coffee" (in kitchen): wanted Coffee Maker: on; got handed off (not about the home)
- "what's the temperature in here" (in bathroom): wanted Bathroom Heating: ask the temperature; got handed off (not about the home)
- "is the light on" (in kitchen): wanted Kitchen Light: ask how it is; got handed off (not supported)
- "turn on the music" (in living room): wanted TV: on; got handed off (not about the home)
- "Licht aus" (in bedroom): wanted Desk Lamp: off; got Desk Lamp: ask how it is · 0.20
- "schließ die Rollläden" (in living room): wanted Living Room Blinds: close; got handed off (no value)
- "mach die Kaffeemaschine an" (in hallway): wanted Coffee Maker: on; got handed off (not about the home)
- "wie warm ist es hier" (in bathroom): wanted Bathroom Heating: ask the temperature; got handed off (not about the home)
- "mach es heller, 70 Prozent" (in living room): wanted Floor Lamp: to 70 %; got handed off (not about the home)
- "turn off the left light but turn on the right one" (in living room): wanted Left Light: off, Right Light: on; got Left Light: off, Right Light: off · 0.28
- "mach das linke Licht aus, aber das rechte an" (in living room): wanted Left Light: off, Right Light: on; got Left Light: off, Right Light: off · 0.24
- "schalte alle Lichter auf dieser Etage ein" (in living room): wanted Hallway Light: on, Kitchen Light: on, Floor Lamp: on, Left Light: on, Right Light: on; got Floor Lamp: on, Left Light: on, Right Light: on · 0.51
- "schließ alle Rollläden im Obergeschoss": wanted Kids Room Blinds: close, Office Blinds: close; got handed off (no value)
- "it's too dark in here" (in kitchen): wanted Kitchen Light: on; got Kitchen Light: ask how it is · 0.25
- "hier ist es viel zu dunkel" (in bedroom): wanted Desk Lamp: on; got Desk Lamp: ask how it is · 0.66
- "turn off the tv, close the living room blinds and set the thermostat to 21 degrees": wanted Thermostat: to 21 °, Living Room Blinds: close, TV: off; got handed off (value doesn't fit)
- "schalte den Fernseher und die Stehlampe aus und öffne den Rollladen Wohnzimmer": wanted Living Room Blinds: open, Floor Lamp: off, TV: off; got Living Room Blinds: open, Floor Lamp: off, TV: on · 0.17
- "wie spät ist es" (in living room): wanted handed off; got Living Room Blinds: ask how it is · 0.57
- "was ist die Hauptstadt von Frankreich" (in bedroom): wanted handed off; got Desk Lamp: ask how it is · 0.89
- "turn off all the lights in the house": wanted Desk Lamp: off, Hallway Light: off, Kitchen Light: off, Floor Lamp: off, Left Light: off, Right Light: off; got handed off (no device or room found)
- "fahr alle Rollos im zweiten Stock runter": wanted Kids Room Blinds: close, Office Blinds: close; got handed off (not supported)
- "make it 23" (after "set the bathroom heating to 21 degrees"): wanted Bathroom Heating: to 23 °; got handed off (not about the home)
- "Rollos runter" (in living room): wanted Living Room Blinds: close; got Living Room Blinds: ask how it is · 0.17
- "mach die Rollläden im Wohnzimmer auf": wanted Living Room Blinds: open; got handed off (no value)
- "Licht im Flur aus": wanted Hallway Light: off; got Hallway Light: ask how it is · 0.31
- "schalte den Fernseher ab": wanted TV: off; got TV: on · 0.68
- "mach das rechte Licht an" (in living room): wanted Right Light: on; got Right Light: ask how it is · 0.26

</details>

<details><summary>Laya multilingual: 33</summary>

- "open the blinds to 30 percent": wanted Living Room Blinds: to 30 %; got Living Room Blinds: open · 0.98
- "mach das Licht im Flur aus": wanted Hallway Light: off; got Hallway Light: on · 0.92
- "mach das Küchenlicht an und den Fernseher aus": wanted Kitchen Light: on, TV: off; got Kitchen Light: on, TV: on · 0.14
- "mach die Stehlampe und das Küchenlicht aus": wanted Kitchen Light: off, Floor Lamp: off; got Kitchen Light: off, Floor Lamp: on · 0.28
- "open the blinds to 30 percent" (in living room): wanted Living Room Blinds: to 30 %; got Living Room Blinds: open · 0.98
- "Licht aus" (in bedroom): wanted Desk Lamp: off; got Desk Lamp: ask how it is · 0.40
- "mach das Licht im Schlafzimmer aus" (in kitchen): wanted Desk Lamp: off; got Desk Lamp: on · 0.98
- "turn on all the lights on the floor" (in living room): wanted Hallway Light: on, Kitchen Light: on, Floor Lamp: on, Left Light: on, Right Light: on; got Floor Lamp: on, Left Light: on, Right Light: on · 0.22
- "close all the covers on the second floor": wanted Kids Room Blinds: close, Office Blinds: close; got Office Blinds: close · 0.48
- "schalte alle Lichter im Wohnzimmer ein": wanted Floor Lamp: on, Left Light: on, Right Light: on; got Thermostat: on · 0.38
- "mach das linke Licht aus, aber das rechte an" (in living room): wanted Left Light: off, Right Light: on; got Left Light: on, Right Light: on · 0.96
- "schalte alle Lichter auf dieser Etage ein" (in living room): wanted Hallway Light: on, Kitchen Light: on, Floor Lamp: on, Left Light: on, Right Light: on; got Thermostat: on · 0.64
- "it's too dark in here" (in kitchen): wanted Kitchen Light: on; got Kitchen Light: ask how it is · 0.26
- "hier ist es viel zu dunkel" (in bedroom): wanted Desk Lamp: on; got Desk Lamp: ask how it is · 0.93
- "I'm freezing" (in bathroom): wanted handed off; got Bathroom Heating: ask the temperature · 0.75
- "mir ist zu warm" (in living room): wanted handed off; got Thermostat: ask the temperature · 0.09
- "mach das Küchenlicht an, die Stehlampe aus und stell die Heizung Bad auf 23 Grad": wanted Bathroom Heating: to 23 °, Kitchen Light: on, Floor Lamp: off; got Bathroom Heating: to 23 °, Kitchen Light: on, Floor Lamp: on · 0.30
- "schalte den Fernseher und die Stehlampe aus und öffne den Rollladen Wohnzimmer": wanted Living Room Blinds: open, Floor Lamp: off, TV: off; got Living Room Blinds: open, Floor Lamp: on, TV: on · 0.30
- "Schreibtischlampe auf 40 Prozent und Küchenlicht aus": wanted Desk Lamp: to 40 %, Kitchen Light: off; got Desk Lamp: ask how it is, Kitchen Light: ask how it is · 0.14
- "tell me a joke" (in kitchen): wanted handed off; got Coffee Maker: off · 0.14
- "set a timer for ten minutes" (in kitchen): wanted handed off; got Coffee Maker: on · 0.02
- "play some jazz" (in living room): wanted handed off; got TV: on · 0.47
- "turn off all the lights in the house": wanted Desk Lamp: off, Hallway Light: off, Kitchen Light: off, Floor Lamp: off, Left Light: off, Right Light: off; got handed off (no device or room found)
- "schalte alle Lichter im Erdgeschoss aus": wanted Hallway Light: off, Kitchen Light: off, Floor Lamp: off, Left Light: off, Right Light: off; got Coffee Maker: on · 0.69
- "fahr alle Rollos im zweiten Stock runter": wanted Kids Room Blinds: close, Office Blinds: close; got Kids Room Blinds: open, Office Blinds: open · 0.85
- "turn on the lights" (in living room): wanted Floor Lamp: on, Left Light: on, Right Light: on; got Floor Lamp: on · 0.22
- "mach die Lichter im Wohnzimmer aus": wanted Floor Lamp: off, Left Light: off, Right Light: off; got Left Light: on · 0.27
- "mach sie auf 50 Prozent" (after "schalte die Schreibtischlampe ein"): wanted Desk Lamp: to 50 %; got Desk Lamp: on · 0.46
- "did I lock the front door": wanted Front Door: ask how it is; got Front Door: lock · 0.00
- "sperr die Haustür auf": wanted Front Door: unlock; got Front Door: lock · 0.00
- "open the door" (in hallway): wanted handed off; got Hallway Light: on · 1.00
- "schalte den Fernseher ab": wanted TV: off; got TV: on · 0.98
- "schalte das linke Licht und die Stehlampe aus": wanted Floor Lamp: off, Left Light: off; got Floor Lamp: on, Left Light: on · 0.26

</details>

<details><summary>Laya English: 12</summary>

- "open the blinds to 30 percent": wanted Living Room Blinds: to 30 %; got Living Room Blinds: open · 0.78
- "open the blinds to 30 percent" (in living room): wanted Living Room Blinds: to 30 %; got Living Room Blinds: open · 0.78
- "is the light on" (in kitchen): wanted Kitchen Light: ask how it is; got handed off (not supported)
- "turn off the left light but turn on the right one" (in living room): wanted Left Light: off, Right Light: on; got Left Light: off, Right Light: off · 0.29
- "turn on all the lights on the floor" (in living room): wanted Hallway Light: on, Kitchen Light: on, Floor Lamp: on, Left Light: on, Right Light: on; got Floor Lamp: on, Left Light: on, Right Light: on · 0.38
- "it's too dark in here" (in kitchen): wanted Kitchen Light: on; got Kitchen Light: ask how it is · 0.03
- "I'm freezing" (in bathroom): wanted handed off; got Bathroom Heating: ask the temperature · 0.92
- "tell me a joke" (in kitchen): wanted handed off; got Kitchen Light: on · 0.18
- "set a timer for ten minutes" (in kitchen): wanted handed off; got Coffee Maker: on · 0.05
- "play some jazz" (in living room): wanted handed off; got TV: on · 0.01
- "turn off all the lights in the house": wanted Desk Lamp: off, Hallway Light: off, Kitchen Light: off, Floor Lamp: off, Left Light: off, Right Light: off; got handed off (no device or room found)
- "open the door" (in hallway): wanted handed off; got Hallway Light: on · 0.45

</details>


## Limits of this benchmark

- **106 sentences and one test home.** A difference of one or two sentences between models or
  versions is noise.
- **The tuning sentences flatter the current version**, and so do the suggested checks, which were
  picked on them. The unseen sentences are the fairer comparison. Check your own commands in the
  server's live log.
- **Intern-Decision answers depend on the other questions in the same call.** Small changes to the
  questions can move its results by a sentence or two.
- **Time and memory were measured on the Mac only.** "Memory in use" is what the Apple graphics
  driver reports. "Peak RAM while loading" is the most memory the process used. 1 GB here is
  2³⁰ bytes.
- **The d1 providers rebuild the models' prompt and, for d1-omni-600M, the network** in the
  server's own code, from the checkpoints' published code. They were not compared number by number
  with Liquid AI's own code.

## The models

| Model | `--model` | Made by | Built on | License |
|---|---|---|---|---|
| [d1-3B](https://huggingface.co/LiquidAI/d1-3B) | `d1-3b` | Liquid AI | LFM2.5-VL-3B | LFM Open License v1.0 |
| [Intern-Decision 2B](https://huggingface.co/internlm/Intern-Decision-2B) | `intern-decision-2b` | InternLM | Qwen3.5-2B | Apache-2.0 |
| [Intern-Decision 0.8B](https://huggingface.co/internlm/Intern-Decision-0.8B) | `intern-decision-0.8b` | InternLM | Qwen3.5-0.8B | Apache-2.0 |
| [d1-omni-600M](https://huggingface.co/LiquidAI/d1-omni-600M) | `d1-omni-600m` | Liquid AI | LFM2.5-Encoder-350M + decision head | LFM Open License v1.0 |
| [Laya multilingual](https://huggingface.co/convaiinnovations/laya-multilingual) | `multilingual` | ConvAI Innovations | mmBERT-base | Apache-2.0 |
| [Laya English](https://huggingface.co/convaiinnovations/laya) | `english` | ConvAI Innovations | ModernBERT-large | Apache-2.0 |

Every download is pinned to a reviewed commit (`server/assist_decider_server/providers.py`). For
Intern-Decision and d1, the server runs no code from the download. The LFM Open License is free to
use unless your organization makes $10M or more a year.

## Repeat the benchmark

From the `server` folder:

```bash
# the current version: right / handed off / wrong at every check, and every mistake.
# One model per process; the big ones take a few minutes each.
for m in multilingual english intern-decision-0.8b intern-decision-2b d1-3b d1-omni-600m; do
    uv run python tests/eval/benchmark.py $m    # --out=FILE also saves every result as JSON
done

# memory and speed, one process per model
for m in multilingual english intern-decision-0.8b intern-decision-2b d1-3b d1-omni-600m; do
    uv run python tests/eval/measure_memory.py $m
done
```

For the previous version, check out commit `1fd399e` in a second folder (`git worktree add
../prev 1fd399e`), copy the current `tests/eval/benchmark.py` into it, and run the same loop
there with this folder's environment.
