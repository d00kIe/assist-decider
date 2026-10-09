"""d1-omni-600M's text path, rebuilt from the checkpoint's encoder.py and prompt.py (commit 02b55d7)
so none of its remote code runs and the vision and audio towers are never loaded.

A bidirectional LFM2.5 encoder reads
`<bos> <state> state <q> question (<opt> <mask> option </opt>)... <decide>`, and a two-layer head
scores the hidden state at every `<mask>`. Imported by the provider after torch is set up.
"""

from __future__ import annotations

import json
import re
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

DELIM = {
    "state": "<|reserved_7|>",
    "q": "<|reserved_8|>",
    "opt": "<|reserved_9|>",
    "opt_end": "<|reserved_10|>",
    "decide": "<|reserved_11|>",
}
MARKER = "<|mask|>"
MAX_LEN = 16384
_SPECIAL = re.compile(r"<\|([A-Za-z0-9_]+)\|>")


def escape(text: str) -> str:
    """`<|name|>` -> `<¦name¦>`, so caller text can't emit a delimiter or marker token."""
    return _SPECIAL.sub(r"<¦\1¦>", text)


def temperature_key(options: int) -> str:
    k = options
    return "choice:" + ("2" if k <= 2 else "3-5" if k <= 5 else "6-10" if k <= 10 else "11+")


def encode(
    tok: Any, state: Any, instructions: str, options: dict[str, str], per_option: int = 24
) -> tuple[list[int], list[int]]:
    """Token ids of one choice question over one state, and each option's marker position."""
    ids_of = tok.convert_tokens_to_ids

    def enc(s: str) -> list[int]:
        return tok(escape(s), add_special_tokens=False)["input_ids"]

    opts = [k if not v else f"{k}: {v}" for k, v in options.items()]
    budget = max(96, min(len(opts) * per_option + 32, MAX_LEN // 2))
    per = max(2, (budget - 3 * len(opts)) // len(opts))
    question = ([ids_of(DELIM["q"])] + enc(instructions))[: max(16, budget)]
    markers = []
    for text in opts:
        markers.append(len(question) + 1)
        question += [ids_of(DELIM["opt"]), ids_of(MARKER), *enc(" " + text)[:per]]
        question.append(ids_of(DELIM["opt_end"]))
    question.append(ids_of(DELIM["decide"]))
    room = max(0, MAX_LEN - len(question) - 2)
    state_ids = [ids_of(DELIM["state"])] + enc(json.dumps(state, ensure_ascii=False))[:room]
    return [tok.bos_token_id, *state_ids, *question], [m + 1 + len(state_ids) for m in markers]


class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float):
        super().__init__()
        self.weight = nn.Parameter(torch.ones(dim))
        self.eps = eps

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        dtype = x.dtype
        x = x.float()
        return self.weight * (x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + self.eps)).to(dtype)


def _rotate_half(x: torch.Tensor) -> torch.Tensor:
    x1, x2 = x.chunk(2, dim=-1)
    return torch.cat((-x2, x1), dim=-1)


class Attention(nn.Module):
    def __init__(self, cfg: dict):
        super().__init__()
        d = cfg["hidden_size"]
        self.heads, self.kv_heads = cfg["num_attention_heads"], cfg["num_key_value_heads"]
        self.head_dim = d // self.heads
        self.q_proj = nn.Linear(d, self.heads * self.head_dim, bias=False)
        self.k_proj = nn.Linear(d, self.kv_heads * self.head_dim, bias=False)
        self.v_proj = nn.Linear(d, self.kv_heads * self.head_dim, bias=False)
        self.out_proj = nn.Linear(self.heads * self.head_dim, d, bias=False)
        self.q_layernorm = RMSNorm(self.head_dim, cfg["norm_eps"])
        self.k_layernorm = RMSNorm(self.head_dim, cfg["norm_eps"])

    def forward(self, x, cos, sin):
        b, length, _ = x.shape
        shape = (b, length, -1, self.head_dim)
        q = self.q_layernorm(self.q_proj(x).view(shape)).transpose(1, 2)
        k = self.k_layernorm(self.k_proj(x).view(shape)).transpose(1, 2)
        v = self.v_proj(x).view(shape).transpose(1, 2)
        q, k = q * cos + _rotate_half(q) * sin, k * cos + _rotate_half(k) * sin
        groups = self.heads // self.kv_heads
        k, v = k.repeat_interleave(groups, dim=1), v.repeat_interleave(groups, dim=1)
        # one unpadded row with no media prefix: every token sees every token, no mask
        y = F.scaled_dot_product_attention(q, k, v, scale=self.head_dim**-0.5)
        return self.out_proj(y.transpose(1, 2).reshape(b, length, -1))


class ShortConv(nn.Module):
    """`out(C * conv(B * x))` with a centred 3-tap depthwise convolution."""

    def __init__(self, cfg: dict):
        super().__init__()
        d = cfg["hidden_size"]
        self.conv = nn.Conv1d(d, d, cfg["conv_L_cache"], groups=d, bias=False)
        self.in_proj = nn.Linear(d, 3 * d, bias=False)
        self.out_proj = nn.Linear(d, d, bias=False)

    def forward(self, x):
        b, c, u = self.in_proj(x).transpose(-1, -2).chunk(3, dim=-2)
        y = F.conv1d(b * u, self.conv.weight, padding=1, groups=self.conv.weight.shape[0])
        return self.out_proj((c * y).transpose(-1, -2))


class MLP(nn.Module):
    def __init__(self, cfg: dict):
        super().__init__()
        hidden = int(cfg["block_ffn_dim_multiplier"] * int(2 * cfg["intermediate_size"] / 3))
        step = cfg["block_multiple_of"]
        hidden = step * ((hidden + step - 1) // step)
        d = cfg["hidden_size"]
        self.w1 = nn.Linear(d, hidden, bias=False)
        self.w3 = nn.Linear(d, hidden, bias=False)
        self.w2 = nn.Linear(hidden, d, bias=False)

    def forward(self, x):
        return self.w2(F.silu(self.w1(x)) * self.w3(x))


class Layer(nn.Module):
    def __init__(self, cfg: dict, kind: str):
        super().__init__()
        self.is_attention_layer = kind == "full_attention"
        if self.is_attention_layer:
            self.self_attn = Attention(cfg)
        else:
            self.conv = ShortConv(cfg)
        self.feed_forward = MLP(cfg)
        self.operator_norm = RMSNorm(cfg["hidden_size"], cfg["norm_eps"])
        self.ffn_norm = RMSNorm(cfg["hidden_size"], cfg["norm_eps"])


class Trunk(nn.Module):
    def __init__(self, cfg: dict):
        super().__init__()
        self.embed_tokens = nn.Embedding(cfg["vocab_size"], cfg["hidden_size"])
        self.layers = nn.ModuleList(Layer(cfg, kind) for kind in cfg["layer_types"])
        self.embedding_norm = RMSNorm(cfg["hidden_size"], cfg["norm_eps"])
        self.rope_theta = cfg["rope_theta"]
        self.head_dim = cfg["hidden_size"] // cfg["num_attention_heads"]

    def forward(self, ids: torch.Tensor) -> torch.Tensor:
        h = self.embed_tokens(ids)
        exponent = torch.arange(0, self.head_dim, 2, dtype=torch.int64).float() / self.head_dim
        inv_freq = (1.0 / (self.rope_theta**exponent)).to(h.device)
        positions = torch.arange(ids.shape[1], device=h.device).float()
        freqs = (inv_freq[None, :, None] @ positions[None, None, :]).transpose(1, 2)
        emb = torch.cat((freqs, freqs), dim=-1)
        cos, sin = emb.cos().to(h.dtype)[:, None], emb.sin().to(h.dtype)[:, None]
        for layer in self.layers:
            x = layer.operator_norm(h)
            x = layer.self_attn(x, cos, sin) if layer.is_attention_layer else layer.conv(x)
            h = h + x
            h = h + layer.feed_forward(layer.ffn_norm(h))
        return self.embedding_norm(h)


class DecisionHead(nn.Module):
    def __init__(self, d: int, layers: int):
        super().__init__()
        self.type_emb = nn.Embedding(3, d)  # choice, score, noul
        layer = nn.TransformerEncoderLayer(
            d, d // 64, 4 * d, 0.0, batch_first=True, norm_first=True
        )
        self.head = nn.TransformerEncoder(layer, layers, enable_nested_tensor=False)
        self.scorer = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, d), nn.GELU(), nn.Linear(d, 1))

    def forward(self, h: torch.Tensor, markers: torch.Tensor) -> torch.Tensor:
        h = h + self.type_emb.weight[0]  # every question here is a choice
        for layer in self.head.layers:
            h = layer(h)
        return self.scorer(h[0, markers]).squeeze(-1).float()


class D1Omni(nn.Module):
    """Trunk and head with the checkpoint's parameter names (`encoder.*`, `head.*`)."""

    def __init__(self, config: dict):
        super().__init__()
        self.encoder = Trunk(config["text_config"])
        self.head = DecisionHead(config["text_config"]["hidden_size"], config["head_layers"])

    def forward(self, ids: torch.Tensor, markers: torch.Tensor) -> torch.Tensor:
        return self.head(self.encoder(ids), markers)
