#!/usr/bin/env python
"""地形可通行性分割器：训练 + 全图推理（2026-10-08）—— 阶段 B / C。

★ 为什么要专门训一个
-------------------
原神美术图**不含可通行性信息**。通用 VL（deepseek / qwen）全部证伪 ——
它们把等高线当路。根因不是模型不够强，而是**任务错配**：让模型「看图说路」
等于让它猜，而「哪些像素能踩」是一个**像素级、纯视觉纹理**的判别问题，
专用分割器做的正是这件事。

★ 掩码三态（务必读）
--------------------
    255 = 未标注 -> ``ignore_index=255``，**不进 loss 也不进指标**
      1 = 可通行
      0 = 不可通行

    人只标了路附近几笔。如果把 255 当负样本，模型会学成
    「没画过的一律不能走」。

★ 验证必须肉眼
--------------
loss 好看不代表分割对。所以固定导出 10 张**三联图**（原图 | 真值 | 预测）
到 ``%TEMP%\\terrain_val_<id>.png``，**训练完请自己打开看**。

用法::

    # 训练（默认 MobileNetV3-Small，CPU 也能跑）
    python train_terrain.py

    # 跑久一点 / 换骨干
    python train_terrain.py --epochs 40 --backbone resnet18
    python train_terrain.py --pretrained          # 要下权重（联网）

    # 只跑全图推理（阶段 C）
    python train_terrain.py --infer-only
"""

from __future__ import annotations

# ── 发布包路径解析 ─────────────────────────────────────────────────
# ★ 这段由发布构建注入，替代原先散落各处的本机绝对路径。
#   优先级：环境变量 > 从本文件向上找仓库根 > 相对当前目录（响亮失败）。
#   刻意用 ``_pl`` / ``_os`` 私有别名：这些脚本五花八门，
#   用 ``Path`` / ``os`` 直接定义会和它们各自的 import 打架。
import os as _os
import pathlib as _pl


def _neko_repo_root() -> "_pl.Path":
    for _p in _pl.Path(__file__).resolve().parents:
        if (_p / "recognition" / "configs" / "rois.toml").is_file():
            return _p
    return _pl.Path(__file__).resolve().parents[0]


_REPO_ROOT = _neko_repo_root()
RECOG_ROOT = _pl.Path(_os.environ.get("NEKO_RECOGNITION_ROOT", "").strip()
                      or (_REPO_ROOT / "recognition"))
PLUGIN_DIR = _pl.Path(_os.environ.get("NEKO_PLUGIN_DIR", "").strip()
                      or (_REPO_ROOT / "plugin" / "plugins" / "genshin_game_buddy"))
CONSOLE_DIR = _pl.Path(_os.environ.get("NEKO_CONSOLE_DIR", "").strip()
                       or (_REPO_ROOT / "console"))

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                     # pragma: no cover
    pass

LABEL_DIR = Path(RECOG_ROOT / 'data' / 'terrain_labels')
PATCH_DIR = Path(RECOG_ROOT / 'data' / 'terrain_patches')
MODEL_DIR = Path(RECOG_ROOT / 'models')
BASE_MAP = Path(RECOG_ROOT / 'data' / 'mihoyo_maps' / 'teyvat_P0.png')
PASSABLE_PNG = Path(RECOG_ROOT / 'data' / 'terrain_passable.png')
TRAIN_JSON = MODEL_DIR / "terrain_v0.json"

IGNORE, PASS, BLOCK = 255, 1, 0
VAL_RATIO = 0.2
INFER_SCALE = 8          # 全图太大，先缩到 1/8 再推理


def hr(t: str) -> None:
    print("\n" + "=" * 74 + f"\n  {t}\n" + "=" * 74)


# ---------------------------------------------------------------- 数据
def collect() -> list[tuple[str, Path, Path]]:
    if not LABEL_DIR.is_dir():
        raise SystemExit(f"✗ 没有 {LABEL_DIR} —— 先跑 label_terrain.py 标一批。")
    rows = []
    for lp in sorted(LABEL_DIR.glob("*.png")):
        pp = PATCH_DIR / lp.name
        if pp.is_file():
            rows.append((lp.stem, lp, pp))
    return rows


def split(rows: list, seed: int = 20261008):
    """按 id 哈希做**确定性**切分。

    ★ 为什么不用随机：同一份数据反复跑必须是同一个划分，否则
      「验证集准确率」每次都不一样，根本没法比较两个模型。
    """
    rs = list(rows)
    random.Random(seed).shuffle(rs)
    n_val = max(1, int(len(rs) * VAL_RATIO))
    return rs[n_val:], rs[:n_val]


class PatchSet:
    """标注 patch 数据集。

    ★ 为什么不在类定义里继承 ``torch.utils.data.Dataset``
      本模块支持「没装 torch 也能 import」（阶段 D 的规划器要复用里面的
      常量）。torch 在 :meth:`__getitem__` 里惰性导入。
    """

    def __init__(self, rows, crop: int, train: bool, seed: int = 0):
        self.rows, self.crop, self.train = rows, crop, train
        self.rng = random.Random(seed)

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        import torch
        _, lp, pp = self.rows[i]
        img = np.array(Image.open(pp).convert("RGB"))
        msk = np.array(Image.open(lp).convert("L"))
        H, W = msk.shape
        c = min(self.crop, H, W)
        if self.train:
            # ★ 只裁**含标注**的窗口。全图大部分是 255，盲裁会拿到
            #   一张全 IGNORE 的 patch，梯度是 0，白白耗一个 iteration。
            best, best_n = (0, 0), -1
            for _ in range(10):
                y = self.rng.randint(0, H - c)
                x = self.rng.randint(0, W - c)
                n = int((msk[y:y + c, x:x + c] != IGNORE).sum())
                if n > best_n:
                    best, best_n = (y, x), n
                if n > c * c * 0.25:
                    break
            y, x = best
        else:
            y, x = max(0, (H - c) // 2), max(0, (W - c) // 2)
        im = img[y:y + c, x:x + c]
        mk = msk[y:y + c, x:x + c]

        if self.train:
            k = self.rng.randint(0, 3)                     # rot90
            im, mk = np.rot90(im, k), np.rot90(mk, k)
            if self.rng.random() < 0.5:                   # hflip
                im, mk = im[:, ::-1], mk[:, ::-1]
            if self.rng.random() < 0.5:                   # vflip
                im, mk = im[::-1], mk[::-1]

        im = np.ascontiguousarray(im).astype(np.float32) / 255.0
        im = (im - 0.5) / 0.5                            # 简单归一化
        mk = np.ascontiguousarray(mk).astype(np.int64)

        x = torch.from_numpy(im).permute(2, 0, 1)
        yv = torch.from_numpy(mk)
        if self.train:
            x = x * float(torch.rand(1) * 0.2 + 0.9)     # 亮度抖动
        return x, yv


# ---------------------------------------------------------------- 模型
def build_model(arch: str = "mobilenet_v3_small", pretrained: bool = False):
    import torch
    import torch.nn as nn
    import torchvision.models as tvm

    w = "DEFAULT" if pretrained else None
    try:
        net = (tvm.mobilenet_v3_small(weights=w) if arch.startswith("mobilenet")
               else tvm.resnet18(weights=w))
    except Exception as exc:                              # noqa: BLE001
        print(f"⚠ 加载预训练权重失败（{exc}），改用随机初始化。")
        net = (tvm.mobilenet_v3_small(weights=None) if arch.startswith("mobilenet")
               else tvm.resnet18(weights=None))

    if arch.startswith("mobilenet"):
        body, ch = net.features, 576
    else:
        body = nn.Sequential(net.conv1, net.bn1, net.relu, net.maxpool,
                             net.layer1, net.layer2, net.layer3, net.layer4)
        ch = 512
    return nn.Sequential(body, nn.Conv2d(ch, 2, 1))


def iou_and_acc(logit, tgt):
    import torch
    with torch.no_grad():
        pred = logit.argmax(1)
        valid = tgt != IGNORE
        p, t = pred[valid], tgt[valid]
        acc = (p == t).float().mean().item() if valid.any() else 0.0
        ious = []
        for c in (PASS, BLOCK):
            pi, ti = (p == c), (t == c)
            u = (pi | ti).sum().item()
            ious.append((pi & ti).sum().item() / u if u else float("nan"))
    return acc, ious


# ---------------------------------------------------------------- 阶段 B
def train(args) -> dict:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader

    rows = collect()
    if len(rows) < 5:
        raise SystemExit(f"✗ 只有 {len(rows)} 张标注，最少要 5 张。"
                         f"目标 50~100 张。")
    tr, va = split(rows, args.seed)
    hr(f"数据：共 {len(rows)} 张 / 训练 {len(tr)} / 验证 {len(va)}"
       f"（VAL_RATIO={VAL_RATIO}，确定性切分 seed={args.seed}）")

    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if dev.type == "cpu":
        print("⚠ torch 是 CPU 版，训练会慢很多。")
        print("  要用 N 卡：pip install torch torchvision --index-url "
              "https://download.pytorch.org/whl/cu121")

    dtr = DataLoader(PatchSet(tr, args.crop, True, args.seed),
                     batch_size=args.batch, shuffle=True, num_workers=0)
    dva = DataLoader(PatchSet(va, args.crop, False),
                     batch_size=1, shuffle=False, num_workers=0)

    # 类权重：先扫一遍统计 PASS/BLOCK 比例
    cnt = [0, 0]
    for _, lp, _ in rows:
        m = np.array(Image.open(lp).convert("L"))
        cnt[0] += int((m == PASS).sum())
        cnt[1] += int((m == BLOCK).sum())
    tot = max(1, cnt[0] + cnt[1])
    print(f"  像素分布：可通行 {cnt[0] / tot * 100:.1f}%  "
          f"不可通行 {cnt[1] / tot * 100:.1f}%")
    w = torch.tensor([tot / max(1, 2 * cnt[0]), tot / max(1, 2 * cnt[1])],
                     dtype=torch.float32, device=dev)
    print(f"  类权重：{[round(float(x), 2) for x in w]}")

    net = build_model(args.backbone, args.pretrained).to(dev)
    opt = torch.optim.AdamW(net.parameters(), lr=args.lr, weight_decay=1e-4)
    sch = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    lossf = nn.CrossEntropyLoss(weight=w, ignore_index=IGNORE)

    hist = []
    t0 = time.monotonic()
    for ep in range(args.epochs):
        net.train()
        tl, nb = 0.0, 0
        for x, y in dtr:
            x, y = x.to(dev), y.to(dev)
            if (y != IGNORE).sum() == 0:
                continue
            opt.zero_grad()
            loss = lossf(net(x), y)
            loss.backward()
            opt.step()
            tl += float(loss)
            nb += 1
        sch.step()
        net.eval()
        accs, ious = [], []
        with torch.no_grad():
            for x, y in dva:
                x, y = x.to(dev), y.to(dev)
                a, io = iou_and_acc(net(x), y)
                accs.append(a)
                ious.append(io)
        macc = float(np.mean(accs)) if accs else 0.0
        mious = [float(np.nanmean([v[i] for v in ious])) if ious else 0.0
                 for i in range(2)]
        tl = tl / max(1, nb)
        hist.append({"epoch": ep + 1, "loss": tl, "val_acc": macc,
                     "iou_pass": mious[0], "iou_block": mious[1]})
        print(f"  epoch {ep + 1:3d}/{args.epochs}  loss {tl:.4f}  "
              f"val_acc {macc * 100:.1f}%  IoU[通行] {mious[0]:.3f}  "
              f"IoU[不通] {mious[1]:.3f}")

    net.eval()
    acc_tr = float(np.mean([h["loss"] for h in hist]))
    final = hist[-1]
    overfit = final["val_acc"]

    hr("验证集预测（请**肉眼确认**，不要只看 loss）")
    dump_preds(net, va, dev, args.val_samples)

    hr("导出")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    ckpt = MODEL_DIR / "terrain_v0.pt"
    torch.save({"arch": args.backbone, "state": net.state_dict(),
                "crop": args.crop}, ckpt)
    print(f"  ✓ {ckpt}  ({ckpt.stat().st_size / 1024:.0f} KB)")
    onnx_path = export_onnx(net, args)
    meta = {"arch": args.backbone, "crop": args.crop,
            "epochs": args.epochs, "val_acc": final["val_acc"],
            "iou_pass": final["iou_pass"], "iou_block": final["iou_block"],
            "n_train": len(tr), "n_val": len(va),
            "train_loss_last": acc_tr, "history": hist,
            "onnx": str(onnx_path) if onnx_path else None,
            "note": "train_loss_last 是训练 loss，不是准确率；"
                    "与 val_acc 的差距过大 = 过拟合"}
    TRAIN_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"  ✓ {TRAIN_JSON}")
    return meta


def dump_preds(net, rows, dev, n: int) -> None:
    """导出验证预测三联图到 %TEMP%。**肉眼确认的唯一依据。**"""
    import torch
    tmp = Path(os.environ.get("TEMP", "."))
    out = []
    net.eval()
    with torch.no_grad():
        for sid, lp, pp in rows[:n]:
            img = Image.open(pp).convert("RGB")
            msk = np.array(Image.open(lp).convert("L"))
            a = np.asarray(img, np.float32) / 127.5 - 1.0
            t = torch.from_numpy(a).permute(2, 0, 1)[None].to(dev)
            pred = net(t).argmax(1)[0].cpu().numpy().astype(np.uint8)
            gt = np.where(msk == IGNORE, 0, msk).astype(np.uint8)
            pr = np.where(pred == PASS, 255, 0).astype(np.uint8)
            g = Image.fromarray(gt).convert("L").point(
                lambda v: 255 if v else 0).convert("RGB")
            p_ = Image.fromarray(pr).convert("L").convert("RGB")
            w, h = img.size
            trip = Image.new("RGB", (w * 3 + 16, h), (30, 30, 46))
            trip.paste(img, (0, 0))
            trip.paste(g, (w + 8, 0))
            trip.paste(p_, (w * 2 + 16, 0))
            fp = tmp / f"terrain_val_{sid}.png"
            trip.save(fp)
            out.append(str(fp))
            print(f"  ✓ {fp}")
    if not out:
        print("  （验证集为空，没导出）")


def export_onnx(net, args):
    import torch
    path = MODEL_DIR / "terrain_v0.onnx"
    dummy = torch.randn(1, 3, args.crop, args.crop)
    try:
        torch.onnx.export(net, dummy, path, input_names=["img"],
                          output_names=["logits"],
                          opset_version=17, dynamo=False)
        print(f"  ✓ {path}  ({path.stat().st_size / 1024:.0f} KB)")
        return path
    except Exception as exc:                              # noqa: BLE001
        print(f"  ⚠ ONNX 导出失败：{exc}")
        print("    torch 2.14 的新导出器需要 onnx / onnxscript 两个包。")
        print("    不想装就继续用 .pt（本项目的 onnxruntime 推理走 "
              "terrain_v0.pt 即可，或手动装：pip install onnx onnxscript）")
        return None


# ---------------------------------------------------------------- 阶段 C
def infer_full_map(args) -> None:
    """全图推理 -> terrain_passable.png（白=可通行）。"""
    import torch

    ck = torch.load(MODEL_DIR / "terrain_v0.pt", map_location="cpu",
                    weights_only=False)
    net = build_model(ck["arch"], False)
    net.load_state_dict(ck["state"])
    net.eval()

    src = BASE_MAP
    if not src.is_file():
        raise SystemExit(f"✗ 底图不存在：{src}")
    hr(f"全图推理（阶段 C）· 先缩到 1/{INFER_SCALE}")
    im = Image.open(src).convert("RGB")
    W, H = im.size
    w, h = W // INFER_SCALE, H // INFER_SCALE
    print(f"  原图 {W}x{H} -> {w}x{h}")
    small = np.asarray(im.resize((w, h), Image.BILINEAR), np.float32) / 127.5 - 1.0

    tile = ck.get("crop", 256)
    pad = tile // 4
    out = np.zeros((h, w), np.float32)
    t0 = time.monotonic()
    done = 0
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            y1, x1 = min(h, y + tile), min(w, x + tile)
            ys, xs = max(0, y - pad), max(0, x - pad)
            ye, xe = min(h, y1 + pad), min(w, x1 + pad)
            sub = small[ys:ye, xs:xe]
            # 边缘块补到固定尺寸，否则 batch 形状不一致
            ph, pw = tile + 2 * pad, tile + 2 * pad
            buf = np.zeros((ph, pw, 3), np.float32)
            buf[:sub.shape[0], :sub.shape[1]] = sub
            t = torch.from_numpy(buf).permute(2, 0, 1)[None]
            pr = net(t)[0].softmax(0)[PASS].numpy()
            # 只取中心区域，避开补边
            oy, ox = (y - ys) + pad, (x - xs) + pad
            out[y:y1, x:x1] = pr[oy:oy + (y1 - y), ox:ox + (x1 - x)]
            done += 1
            if done % 40 == 0:
                print(f"    {done} 块  {time.monotonic() - t0:.0f}s")
    print(f"  ✓ {done} 块，用时 {time.monotonic() - t0:.0f}s")

    gp = (out * 255).clip(0, 255).astype(np.uint8)
    tmp = PASSABLE_PNG.with_suffix(".png.tmp")
    PASSABLE_PNG.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(gp).save(tmp, format="PNG")
    os.replace(tmp, PASSABLE_PNG)
    print(f"  ✓ {PASSABLE_PNG}  {w}x{h}  "
          f"可通行像素占比 {(gp > 127).mean() * 100:.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description="地形分割器 训练 / 全图推理")
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--crop", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--seed", type=int, default=20261008)
    ap.add_argument("--backbone", default="mobilenet_v3_small",
                    choices=["mobilenet_v3_small", "resnet18"])
    ap.add_argument("--pretrained", action="store_true",
                    help="用 ImageNet 预训练权重（需要联网下载）")
    ap.add_argument("--val-samples", type=int, default=10)
    ap.add_argument("--infer-only", action="store_true",
                    help="跳过训练，只跑阶段 C 全图推理")
    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    try:
        import torch
        import torchvision
    except ImportError:
        raise SystemExit("✗ 没装 torch。先跑：pip install torch torchvision "
                         "--index-url https://download.pytorch.org/whl/cu121")
    try:
        import torch as _t
        _t.manual_seed(args.seed)
    except Exception:                                      # noqa: BLE001
        pass

    if not args.infer_only:
        m = train(args)
        hr("训练小结")
        print(f"  backbone      : {m['arch']}")
        print(f"  训练/验证     : {m['n_train']} / {m['n_val']}")
        print(f"  训练 loss(末) : {m['train_loss_last']:.4f}")
        print(f"  验证准确率    : {m['val_acc'] * 100:.1f}%")
        print(f"  验证 IoU      : 通行 {m['iou_pass']:.3f} / "
              f"不通 {m['iou_block']:.3f}")
        print()
        print("  ★ 请打开 %TEMP%\\terrain_val_*.png **肉眼确认**。")
        print("    三联图从左到右：原图 | 真值 | 预测。")
        print("    预测里路的走向和真值对不上 = 模型没学会，别看 loss。")
    hr("阶段 C · 全图推理")
    try:
        infer_full_map(args)
    except SystemExit:
        raise
    except Exception as exc:                              # noqa: BLE001
        print(f"✗ 全图推理失败：{exc}")
        print("  多半是没训过（缺 terrain_v0.pt）。先跑训练。")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
