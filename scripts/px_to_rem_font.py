"""一次性 codemod: font-size 任意值 px → rem, 让字号随 html 根字号缩放。

背景: 全站字号都是 Tailwind 任意值写法 text-[13px](px 不随根字号缩放), 字体大小
偏好(账户页·显示设置)要生效, 必须先迁到 rem。设计基准 html { font-size: 14px },
所以换算 N/14 rem, 默认档渲染像素级不变。

只动 `text-[Npx]` 字号类; w-[13px]/h-[13px]/p-[12px] 等几何类不缩放, 保持 px。
幂等: 替换后的 rem 值不再命中正则, 重复运行零改动。

用法: python scripts/px_to_rem_font.py [--dry-run]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "frontend" / "src"
BASE_PX = 14  # index.css 的 html 设计基准

# (?<![A-Za-z-]) 防止误中 context-[12px] 之类的前缀粘连; text-[Npx] 支持 N 位小数
PAT = re.compile(r"(?<![A-Za-z-])text-\[(\d+(?:\.\d+)?)px\]")
# 畸形变体: px 写在括号外(text-[10.5]px) —— Tailwind 不认, 从未生成过样式(79 处, 元素
# 一直在继承父级字号)。按同一换算修成 rem, 恢复作者本意的小字号并纳入字体缩放体系。
PAT_MALFORMED = re.compile(r"(?<![A-Za-z-])text-\[(\d+(?:\.\d+)?)\]px")


def to_rem(n_str: str) -> str:
    r = round(float(n_str) / BASE_PX, 4)
    s = f"{r:.4f}".rstrip("0").rstrip(".")
    return s or "0"


def migrate(text: str) -> tuple[str, int]:
    count = 0

    def sub(m: re.Match) -> str:
        nonlocal count
        count += 1
        return f"text-[{to_rem(m.group(1))}rem]"

    text, n1 = PAT.subn(sub, text)
    text, n2 = PAT_MALFORMED.subn(sub, text)
    return text, n1 + n2


def main() -> None:
    dry = "--dry-run" in sys.argv
    files = sorted(p for p in ROOT.rglob("*") if p.suffix in (".jsx", ".js")
                   and "node_modules" not in p.parts)
    total_files = total_subs = 0
    for p in files:
        text = p.read_text(encoding="utf-8")
        new, n = migrate(text)
        if n:
            total_files += 1
            total_subs += n
            rel = p.relative_to(ROOT.parent.parent)
            print(f"{n:5d}  {rel}")
            if not dry:
                p.write_text(new, encoding="utf-8")
    print(f"\n{'[dry-run] ' if dry else ''}replaced {total_subs} occurrences "
          f"in {total_files} files")


if __name__ == "__main__":
    main()
