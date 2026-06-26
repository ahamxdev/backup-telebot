"""Entry-point layer — argument parsing and logging setup."""

from __future__ import annotations

import argparse
import logging
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Telegram Backup Sender Bot — watches a directory and uploads new files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        default=None,
        help="Path to a .env file (system env vars always override .env values).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity.",
    )

    sub = parser.add_subparsers(dest="command")

    # Sub-command: list-jobs
    sub.add_parser(
        "list-jobs",
        help="Print all configured backup jobs and their schedules, then exit.",
    )

    # Sub-command: run-job
    run_job_p = sub.add_parser(
        "run-job",
        help="Run a single backup job immediately (ignoring its schedule), then exit.",
    )
    run_job_p.add_argument("name", help="Job name as defined in jobs.toml.")

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )

    from .config import load_settings

    try:
        settings = load_settings(env_file=args.env_file)
    except ValueError as exc:
        logging.critical("Configuration error: %s", exc)
        sys.exit(1)

    if args.command == "list-jobs":
        _cmd_list_jobs(settings)
    elif args.command == "run-job":
        _cmd_run_job(settings, args.name)
    else:
        from .service import BackupSenderService
        BackupSenderService(settings).run()


def _cmd_list_jobs(settings: object) -> None:
    from .jobs import load_jobs
    from .scheduler import make_schedule
    from datetime import datetime, timezone

    jobs_file = getattr(settings, "backup_jobs_file", "")
    if not jobs_file:
        print("No BACKUP_JOBS_FILE configured.")
        sys.exit(0)

    try:
        jobs = load_jobs(jobs_file)
    except Exception as exc:
        print(f"Error loading jobs: {exc}", file=sys.stderr)
        sys.exit(1)

    if not jobs:
        print("No enabled jobs found.")
        sys.exit(0)

    now = datetime.now(tz=timezone.utc)
    print(f"{'Name':<20} {'Schedule':<20} {'Next run (UTC)':<25} Output")
    print("-" * 90)
    for job in jobs:
        try:
            sched = make_schedule(job.schedule)
            next_run = sched.next_after(now).strftime("%Y-%m-%d %H:%M")
        except Exception:
            next_run = "?"
        print(f"{job.name:<20} {job.schedule:<20} {next_run:<25} {job.output}")


def _cmd_run_job(settings: object, job_name: str) -> None:
    from .jobs import load_jobs, run_job
    from .pipeline import process_file
    from .telegram_api import TelegramBotClient
    from pathlib import Path

    jobs_file = getattr(settings, "backup_jobs_file", "")
    if not jobs_file:
        print("No BACKUP_JOBS_FILE configured.", file=sys.stderr)
        sys.exit(1)

    try:
        jobs = load_jobs(jobs_file)
    except Exception as exc:
        print(f"Error loading jobs: {exc}", file=sys.stderr)
        sys.exit(1)

    job = next((j for j in jobs if j.name == job_name), None)
    if job is None:
        names = ", ".join(j.name for j in jobs)
        print(f"Job {job_name!r} not found. Available: {names}", file=sys.stderr)
        sys.exit(1)

    print(f"Running job {job_name!r} …")
    result = run_job(job)
    if not result.success:
        print(f"FAILED: {result.error_message}", file=sys.stderr)
        if result.output_tail:
            print(result.output_tail, file=sys.stderr)
        sys.exit(1)

    print(f"OK — {len(result.output_paths)} output file(s). Processing pipeline …")
    client = TelegramBotClient(
        token=getattr(settings, "telegram_bot_token", ""),
        socks_proxy=getattr(settings, "socks_proxy", ""),
        timeout=getattr(settings, "request_timeout", 120.0),
        api_base_url=getattr(settings, "telegram_api_base_url", "https://api.telegram.org"),
    )

    for output_path in result.output_paths:
        try:
            parts, checksum = process_file(
                output_path,
                compress=job.compress,
                encrypt_recipient=job.encrypt_recipient,
                encrypt_tool=getattr(settings, "default_encrypt_tool", "age"),
                enforce_encryption=job.enforce_encryption,
                split_size_mb=job.split_size_mb,
            )
        except Exception as exc:
            print(f"Pipeline error: {exc}", file=sys.stderr)
            sys.exit(1)

        chat_ids = job.target_chat_ids or getattr(settings, "telegram_target_chat_ids", ())
        print(f"Uploading {len(parts)} part(s) to {len(chat_ids)} chat(s) …")
        for i, part in enumerate(parts, 1):
            for chat_id in chat_ids:
                client.send_document(
                    chat_id=chat_id,
                    file_path=str(part),
                    filename=part.name,
                )
                print(f"  Sent {part.name} → chat {chat_id}")
            part.unlink(missing_ok=True)

    print("Done.")


if __name__ == "__main__":
    main()
