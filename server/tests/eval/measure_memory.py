"""Memory and speed of one decision model: load it, ask 12 single questions, print and save.

Run once per model, each in its own process so the numbers don't mix:
    uv run python tests/eval/measure_memory.py MODEL [DEVICE] [--out=DIR]
"""

import json
import resource
import sys
import time

import torch

from assist_decider_server.providers import Question, make_provider

args = [a for a in sys.argv[1:] if not a.startswith("--")]
model, device = args[0], args[1] if len(args) > 1 else "auto"
out_dir = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--out=")), None)


def peak_rss_gib() -> float:
    # ru_maxrss is bytes on macOS and KiB on Linux
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 2**30 if sys.platform == "darwin" else rss / 2**20


p = make_provider(model, device)
started = time.perf_counter()
p.load()
load_s = time.perf_counter() - started
net = getattr(p, "_model", None) or getattr(p, "_net", None) or getattr(p._agent, "model", None)
weights = sum(t.numel() * t.element_size() for t in net.parameters()) / 2**30
params = sum(t.numel() for t in net.parameters()) / 1e6

q = {
    "intent": Question(
        "Which smart home action does the user ask for?",
        {
            "turn_on": "turn on a device",
            "turn_off": "turn off a device",
            "set_temperature": "set the temperature",
            "none": "something else",
        },
    )
}
texts = [
    "turn off the kitchen light",
    "stell die Heizung auf 22 Grad",
    "what is the capital of france",
    "set the thermostat to 23 degrees and turn on the light in the kitchen",
]
ms = []
for text in texts * 3:
    t = time.perf_counter()
    p.predict({"utterance": text}, q, "en")
    ms.append((time.perf_counter() - t) * 1000)
ms.sort()
res = {
    "model": model,
    "device": p.device,
    "params_m": round(params),
    "weights_gib": round(weights, 2),
    "peak_rss_gib": round(peak_rss_gib(), 2),
    "load_s": round(load_s, 1),
    "question_ms_p50": round(ms[len(ms) // 2]),
}
if p.device == "mps":
    res["mps_driver_gib"] = round(torch.mps.driver_allocated_memory() / 2**30, 2)
if p.device.startswith("cuda"):
    res["cuda_peak_gib"] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
print(json.dumps(res))
if out_dir:
    with open(f"{out_dir}/{model}.memory.json", "w") as f:
        json.dump(res, f, indent=1)
