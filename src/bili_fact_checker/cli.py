"""CLI entry: bili-fact-checker."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bili_fact_checker import __version__
from bili_fact_checker.config import Settings
from bili_fact_checker.ingest import extract_bvid, fetch_transcript, list_subtitles, fetch_video_meta
from bili_fact_checker.pipeline import run_pipeline
from bili_fact_checker.report import dumps_json, to_html, to_markdown


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def cmd_subtitle(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    tr = fetch_transcript(settings, args.input, lang=args.lang, asr=args.asr)
    out = args.output
    if out:
        path = Path(out)
        if path.suffix.lower() == ".json":
            path.write_text(json.dumps(tr.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            path.write_text(tr.to_srt(), encoding="utf-8")
        _err(f"saved {path}")
    else:
        print(tr.to_srt())
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    bvid = extract_bvid(args.input)
    aid, cid, title = fetch_video_meta(settings, bvid)
    _err(f"{bvid} · {title}")
    for s in list_subtitles(settings, aid, cid):
        print(f"[{s.get('lan')}] {s.get('lan_doc')}")
    return 0


def _write_report(report: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(dumps_json(report), encoding="utf-8")
    (out_dir / "report.md").write_text(to_markdown(report), encoding="utf-8")
    (out_dir / "report.html").write_text(to_html(report), encoding="utf-8")
    _err(f"wrote {out_dir}/report.{{json,md,html}}")


def cmd_run(args: argparse.Namespace, tasks: list[str] | None = None) -> int:
    settings = Settings.from_env()
    task_list = tasks or [t.strip() for t in args.tasks.split(",") if t.strip()]
    report = run_pipeline(
        settings,
        args.input,
        tasks=task_list,
        lang=args.lang,
        asr=args.asr,
        log=_err,
    )
    out_dir = Path(args.output or f"output/{report['video']['bvid']}")
    _write_report(report, out_dir)
    if args.print_md:
        print(to_markdown(report))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bili-fact-checker",
        description="Bilibili oral-content analysis & evidence-backed fact checking",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("input", help="BV id or bilibili URL")
    common.add_argument("--lang", default="zh-CN", help="subtitle language preference")
    common.add_argument("--asr", action="store_true", default=True, help="fallback to Whisper if no CC (default on)")
    common.add_argument("--no-asr", action="store_false", dest="asr", help="disable ASR fallback")

    sp = sub.add_parser("subtitle", parents=[common], help="fetch CC / ASR transcript")
    sp.add_argument("-o", "--output", help="output .srt or .json path")
    sp.set_defaults(func=cmd_subtitle)

    lp = sub.add_parser("list", parents=[common], help="list available subtitle tracks")
    lp.set_defaults(func=cmd_list)

    for name, help_text, default_tasks in [
        ("summarize", "generate content summary", ["summary"]),
        ("verify", "extract claims and verify with evidence", ["verify"]),
        ("run", "summary + verify (full pipeline)", ["summary", "verify"]),
    ]:
        rp = sub.add_parser(name, parents=[common], help=help_text)
        rp.add_argument("-o", "--output", help="output directory (default output/<bvid>)")
        rp.add_argument("--tasks", default=",".join(default_tasks), help="comma tasks: summary,claims,verify")
        rp.add_argument("--print-md", action="store_true", help="also print markdown to stdout")
        rp.set_defaults(func=lambda a, _t=default_tasks: cmd_run(a, tasks=None))

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        code = args.func(args)
    except Exception as e:
        _err(f"error: {e}")
        sys.exit(1)
    sys.exit(code or 0)


if __name__ == "__main__":
    main()
