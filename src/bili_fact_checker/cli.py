"""CLI entry: bili-fact-checker."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bili_fact_checker import __version__
from bili_fact_checker.config import Settings, validate_api_bind
from bili_fact_checker.diagnostics import run_doctor
from bili_fact_checker.ingest import (
    extract_bvid,
    fetch_transcript,
    fetch_video_info,
    list_subtitles,
    select_video_part,
)
from bili_fact_checker.pipeline import run_pipeline
from bili_fact_checker.report import dumps_json, to_html, to_markdown


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def cmd_doctor(args: argparse.Namespace) -> int:
    report = run_doctor(Settings.from_env())
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        labels = {"ok": "OK", "warning": "WARN", "error": "ERROR"}
        for check in report.checks:
            print(f"[{labels[check.status]}] {check.name}: {check.message}")
        print("\n可以开始运行。" if report.ready else "\n配置尚未就绪。")
    return 0 if report.ready else 1


def cmd_serve(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    validate_api_bind(args.host, settings.api_token)

    import uvicorn

    uvicorn.run(
        "bili_fact_checker.api.app:create_app",
        host=args.host,
        port=args.port,
        reload=False,
        factory=True,
    )
    return 0


def cmd_subtitle(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    if getattr(args, "transcript", None):
        from bili_fact_checker.ingest import load_transcript_file

        tr = load_transcript_file(
            settings, args.input, args.transcript, page=args.page
        )
    else:
        tr = fetch_transcript(
            settings,
            args.input,
            lang=args.lang,
            asr=args.asr,
            page=args.page,
        )
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
    from bili_fact_checker.ingest import check_bili_login

    ok, info = check_bili_login(settings)
    _err(f"bilibili login: {'OK · ' + info if ok else 'NOT LOGGED IN · ' + info}")
    metadata = fetch_video_info(settings, bvid)
    part = select_video_part(metadata, args.page)
    aid, cid, title = metadata.aid, part.cid, metadata.title
    _err(f"{bvid} · {title} · P{part.page} {part.title}")
    subs, meta = list_subtitles(settings, aid, cid, bvid=bvid)
    if meta.get("need_login_subtitle") and not subs:
        _err("hint: need_login_subtitle=true — refresh SESSDATA to see CC/AI tracks")
    if not subs:
        print("(no subtitle tracks returned)")
    for s in subs:
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
        transcript_file=getattr(args, "transcript", None),
        page=args.page,
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

    doctor = sub.add_parser(
        "doctor", help="check configuration without making paid API calls"
    )
    doctor.add_argument("--json", action="store_true", help="print machine-readable JSON")
    doctor.set_defaults(func=cmd_doctor)

    serve = sub.add_parser("serve", help="run the local API and web interface")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=cmd_serve)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("input", help="BV id or bilibili URL")
    common.add_argument("--lang", default="zh-CN", help="subtitle language preference")
    common.add_argument(
        "--page",
        type=int,
        default=1,
        help="video part number for multipart videos (default: 1)",
    )
    common.add_argument(
        "--asr",
        action="store_true",
        default=True,
        help="no CC → try local faster-whisper (default on)",
    )
    common.add_argument("--no-asr", action="store_false", dest="asr", help="disable local ASR")
    common.add_argument(
        "--transcript",
        metavar="FILE",
        help="use external .srt/.txt/.json instead of CC/ASR (e.g. from VideoCaptioner)",
    )

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
