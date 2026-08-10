"""CLI entry: serve / export / export-dpo / train / benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="agent-guardian")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Start local Daemon")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--db", type=Path, default=Path("agent_guardian.db"))
    serve.add_argument(
        "--terminal-stdin",
        action="store_true",
        help="Also read decisions from Daemon stdin",
    )

    export = sub.add_parser("export", help="Export RESOLVED interventions as DPO JSONL")
    from agent_guardian.exporter import build_export_argparser

    build_export_argparser(export)

    export_dpo = sub.add_parser(
        "export-dpo",
        help="Phase 8 multimodal preference export (Qwen2-VL / LLaVA / ORPO)",
    )
    export_dpo.add_argument("--output", "-o", type=Path, required=True)
    export_dpo.add_argument("--db", type=Path, default=Path("agent_guardian.db"))
    export_dpo.add_argument("--media-dir", type=Path, default=None)
    export_dpo.add_argument(
        "--format",
        choices=["qwen2_vl", "llava", "orpo"],
        default="qwen2_vl",
    )

    train = sub.add_parser(
        "train",
        help="Emit Unsloth / LLaMA-Factory DPO recipe (does not run GPU training)",
    )
    train.add_argument("--dataset", type=Path, required=True, help="DPO JSONL path")
    train.add_argument("--output-dir", type=Path, default=Path("train_out"))
    train.add_argument(
        "--backend", choices=["llamafactory", "unsloth"], default="llamafactory"
    )
    train.add_argument("--model", default="Qwen/Qwen2-VL-2B-Instruct")

    bench = sub.add_parser("benchmark", help="Run 10-task AgentBenchmark comparison")
    bench.add_argument("--output", type=Path, default=Path("benchmark_report.json"))
    bench.add_argument("--seed", type=int, default=0)

    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        from agent_guardian.daemon.app import create_app

        app = create_app(db_path=args.db, enable_terminal_stdin=args.terminal_stdin)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=False)
        return

    if args.command == "export":
        from agent_guardian.exporter import run_export_cli

        raise SystemExit(asyncio.run(run_export_cli(args)))

    if args.command == "export-dpo":

        async def _run() -> int:
            from agent_guardian.align import DatasetCurator

            media = args.media_dir or (args.db.resolve().parent / "agent_guardian_media")
            curator = DatasetCurator(media_root=Path(media), format=args.format)
            stats = await curator.export_from_db(args.db, args.output)
            print(
                f"Exported {stats.written} rows "
                f"(spatial={stats.spatial}, rollbacks={stats.rollbacks}, "
                f"takeovers={stats.takeovers}) → {args.output}"
            )
            return 0

        raise SystemExit(asyncio.run(_run()))

    if args.command == "train":
        from agent_guardian.align import write_train_recipe

        path = write_train_recipe(
            args.dataset,
            args.output_dir,
            backend=args.backend,
            model=args.model,
        )
        print(f"Wrote train recipe → {path}")
        return

    if args.command == "benchmark":
        from agent_guardian.align import AgentBenchmark

        report_path = AgentBenchmark().write_report(args.output, seed=args.seed)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        print(json.dumps(report["modes"], ensure_ascii=False, indent=2))
        print("delta:", report["delta"])
        print(f"report → {report_path}")
        return


if __name__ == "__main__":
    main()
