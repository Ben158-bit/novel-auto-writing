# -*- coding: utf-8 -*-
"""产出完整性监视 / 兜底脚本。

不依赖 Core/Studio 服务，纯读本地文件，检查每章的：
  1. 完整产出（chapter.md 正文 + diff_story_confirmed.md 后验）
  2. txt 导出（<OUTPUT_DIR>/<书>/第N章-标题.txt，路径可用环境变量 OUTPUT_DIR 覆盖）
  3. 微信推送（pipeline_state.json 的 pushed 记录）

对缺失的「导出 / 推送」自动补做（正文缺失需由 pipeline.py 补跑，本脚本不代跑正文）。

用法:
    python verify_output.py                 # 检查最近 2 章并补做缺失收尾
    python verify_output.py --all           # 检查全部章节
    python verify_output.py --chapters 9 10 # 指定区间
    python verify_output.py --check         # 只检查不修复
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pipeline as p


def chapter_title(book_id: str, number: int) -> str:
    body = p.BOOKS_ROOT / book_id / "story" / f"{number:04d}" / "chapter.md"
    if not body.is_file():
        return f"第{number}章"
    first = body.read_text(encoding="utf-8").splitlines()[0]
    return first.lstrip("# ").strip() if first.startswith("#") else f"第{number}章"


def txt_path(book_id: str, number: int) -> Path | None:
    out_dir = p.OUTPUT_DIR / book_id
    if not out_dir.is_dir():
        return None
    for f in out_dir.iterdir():
        if f.is_file() and f.name.startswith(f"第{number}章-") and f.suffix == ".txt":
            return f
    return None


def all_chapter_numbers(book_id: str) -> list[int]:
    story = p.BOOKS_ROOT / book_id / "story"
    if not story.is_dir():
        return []
    nums = sorted(int(d.name) for d in story.iterdir() if d.is_dir() and d.name.isdigit())
    return nums


def check_one(book_id: str, number: int) -> dict:
    complete = p.chapter_complete(book_id, number)
    exported = txt_path(book_id, number) is not None
    state = p.load_state(book_id)
    pushed = number in state.get("pushed", [])
    return {
        "number": number,
        "title": chapter_title(book_id, number),
        "complete": complete,
        "exported": exported,
        "pushed": pushed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="产出完整性监视/兜底")
    parser.add_argument("--book", default=p.DEFAULT_BOOK)
    parser.add_argument("--chapters", nargs=2, type=int, metavar=("START", "END"))
    parser.add_argument("--all", action="store_true", help="检查全部章节")
    parser.add_argument("--daily", type=int, default=2, help="检查最近 N 章（默认 2）")
    parser.add_argument("--check", action="store_true", help="只检查不修复")
    args = parser.parse_args()

    book_id = args.book
    if not p.book_exists(book_id):
        print(f"书不存在：{book_id}")
        return 1

    nums = all_chapter_numbers(book_id)
    if args.all:
        target = nums
    elif args.chapters:
        target = list(range(args.chapters[0], args.chapters[1] + 1))
    else:
        target = nums[-args.daily:] if nums else []

    if not target:
        print("没有可检查的章节")
        return 0

    rows = [check_one(book_id, n) for n in target]
    missing = []
    for r in rows:
        flags = []
        flags.append("正文" if r["complete"] else "缺正文/后验")
        flags.append("txt" if r["exported"] else "缺txt")
        flags.append("微信" if r["pushed"] else "缺微信")
        ok = r["complete"] and r["exported"] and r["pushed"]
        print(f"第{r['number']}章《{r['title']}》: " + ("OK" if ok else "、".join(flags)))
        if not ok:
            missing.append(r)

    if not missing:
        print("\n✅ 全部章节产出完整")
        return 0

    if args.check:
        print(f"\n共 {len(missing)} 章有缺失（--check 模式，不修复）")
        return 0

    # 补做收尾：完整但缺 txt / 缺微信的章节
    fixable = [r for r in missing if r["complete"]]
    body_missing = [r for r in missing if not r["complete"]]
    if body_missing:
        print(f"\n⚠️ 以下章节正文/后验缺失，需用 pipeline.py 补跑：")
        for r in body_missing:
            print(f"  第{r['number']}章《{r['title']}》")
    if fixable:
        print(f"\n补做收尾：{len(fixable)} 章")
        produced = [{"number": f"{r['number']:04d}", "title": r["title"]} for r in fixable]
        p.export_chapters(book_id, produced)
        p.push_chapters(book_id, produced)

    return 0


if __name__ == "__main__":
    sys.exit(main())
