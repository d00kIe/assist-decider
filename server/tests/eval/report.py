"""Markdown tables from the saved results of probe_device_tree.py and measure_memory.py.

Run: uv run python tests/eval/report.py [tests/eval/results]
"""

import json
import sys
from pathlib import Path

DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/eval/results")
ORDER = ["multilingual", "english", "kev-0.8b", "intern-decision-0.8b", "intern-decision-2b"]
NAMES = {
    "multilingual": "Laya multilingual",
    "english": "Laya English",
    "kev-0.8b": "Kev 0.8B",
    "intern-decision-0.8b": "Intern-Decision 0.8B",
    "intern-decision-2b": "Intern-Decision 2B",
}
VARIANTS = {
    "mix": "plain",
    "mix+desc": "+ devices described",
    "mix+num": "+ numbers in options",
    "mix+split": "+ model splits sentence",
    "mix+desc+num+split": "all three",
}

results = {
    m: json.loads((DIR / f"{m}.json").read_text()) for m in ORDER if (DIR / f"{m}.json").exists()
}
memory = {
    m: json.loads((DIR / f"{m}.memory.json").read_text())
    for m in ORDER
    if (DIR / f"{m}.memory.json").exists()
}


def tally(rows, threshold=0.0, lang=None, kind=None):
    rows = [
        r
        for r in rows
        if (lang is None or r["lang"] == lang) and (kind is None or r["set"] == kind)
    ]
    t = {"n": len(rows), "right": 0, "handoff": 0, "wrong": 0, "unsafe": 0}
    for r in rows:
        if r["handoff"] or r["conf"] < threshold:
            t["handoff"] += 1
        elif r["ok"]:
            t["right"] += 1
        else:
            t["wrong"] += 1
            t["unsafe"] += r["unsafe"]
    return t


def cell(t):
    return f"{t['right']}/{t['n']} · {t['handoff']} · **{t['wrong']}**" + (
        f" ({t['unsafe']}🔒)" if t["unsafe"] else ""
    )


def table(header, rows):
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join("---" if i == 0 else "---:" for i in range(len(header))) + "|")
    for r in rows:
        print("| " + " | ".join(str(c) for c in r) + " |")
    print()


print("### Memory and speed\n")
table(
    [
        "Model",
        "Parameters",
        "Weights",
        "Memory in use (graphics)",
        "Peak RAM while loading",
        "Per question",
    ],
    [
        [
            NAMES[m],
            f"{x['params_m']} M",
            f"{x['weights_gib']:.1f} GB",
            f"{x.get('mps_driver_gib', x.get('cuda_peak_gib', 0)):.1f} GB",
            f"{x['peak_rss_gib']:.1f} GB",
            f"{x['question_ms_p50']} ms",
        ]
        for m, x in memory.items()
    ],
)

print("### Whole sentences: today's pipeline against the new approach\n")
for th in (0.0, 0.4):
    print(f"Confidence check at **{th}**:\n")
    table(
        ["Model", "Today's pipeline", "New approach"],
        [
            [NAMES[m], cell(tally(r["today"], th)), cell(tally(r["mix"], th))]
            for m, r in results.items()
        ],
    )

print("### New approach by sentence type and language (no confidence check)\n")
table(
    ["Model", "Device named", "Only a room / nothing", "English", "German"],
    [
        [
            NAMES[m],
            cell(tally(r["mix"], kind="named")),
            cell(tally(r["mix"], kind="room")),
            cell(tally(r["mix"], lang="en")),
            cell(tally(r["mix"], lang="de")),
        ]
        for m, r in results.items()
    ],
)

print("### Confidence check: what each setting does to the new approach\n")
ths = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5)
table(
    ["Model", *[f"check {t}" for t in ths]],
    [[NAMES[m], *[cell(tally(r["mix"], t)) for t in ths]] for m, r in results.items()],
)

print("### Extra context for the model (no confidence check)\n")
table(
    ["Model", *VARIANTS.values()],
    [[NAMES[m], *[cell(tally(r[v])) for v in VARIANTS]] for m, r in results.items()],
)

print("### The model-only tree, step by step\n")
rows = []
for m, r in results.items():
    tr = r.get("tree")
    if not tr:
        continue
    rs = tr["rows"]
    n = len(rs)

    def picked_ok(row, cut):
        return {e for e, p in row["yesno"].items() if p >= cut} == set(row["gold"])

    cuts = [c / 100 for c in range(30, 96, 5)]
    best = max(cuts, key=lambda c: sum(picked_ok(x, c) for x in rs))
    topk = sum(
        set(sorted(x["yesno"], key=x["yesno"].get, reverse=True)[: len(x["gold"])])
        == set(x["gold"])
        for x in rs
    )
    acts = [ok for x in rs for ok in x["action_ok"]]
    vals = [ok for x in rs for ok in x["value_ok"]]
    rows.append(
        [
            NAMES[m],
            f"{sum(picked_ok(x, 0.5) for x in rs)}/{n}",
            f"{sum(picked_ok(x, best) for x in rs)}/{n} (at {best:.2f})",
            f"{topk}/{n}",
            f"{sum(acts)}/{len(acts)}",
            f"{sum(vals)}/{len(vals)}",
            f"{sum(tr['vague_ok'])}/{len(tr['vague_ok'])}",
            f"{sum(x['e2e_ok'] for x in rs)}/{n}",
            sum(x["e2e_unsafe"] for x in rs),
        ]
    )
table(
    [
        "Model",
        "Devices, yes ≥ 0.5",
        "Devices, best cut-off",
        "Devices, top-ranked (count known)",
        "Action per device",
        "Value",
        "Vague change",
        "Whole sentence",
        "Wrong on lock/garage",
    ],
    rows,
)

print("### Time per sentence (median)\n")
table(
    ["Model", "Today's pipeline", "New approach"],
    [
        [
            NAMES[m],
            f"{sorted(x['ms'] for x in r['today'])[len(r['today']) // 2]:.0f} ms",
            f"{sorted(x['ms'] for x in r['mix'])[len(r['mix']) // 2]:.0f} ms",
        ]
        for m, r in results.items()
    ],
)

print("### Every mistake of the new approach (no confidence check)\n")
for m, r in results.items():
    bad = [x for x in r["mix"] if not x["ok"]]
    print(f"**{NAMES[m]}** ({len(bad)})\n")
    for x in bad:
        where = f" (speaker in {x['sat']})" if x["sat"] else ""
        got = "handed to Home Assistant" if x["handoff"] else x["got"]
        print(
            f'- "{x["text"]}"{where}: wanted {x["want"]}, got {got}'
            + (" 🔒" if x["unsafe"] else "")
        )
    print()
