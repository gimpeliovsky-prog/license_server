import argparse
import json
import sys
from typing import Any
from urllib import error, parse, request


def build_headers(admin_token: str) -> dict[str, str]:
    return {
        "X-Admin-Token": admin_token,
        "Accept": "application/json",
    }


def normalize_base_url(value: str) -> str:
    return value.rstrip("/")


def request_json(method: str, url: str, *, admin_token: str, params=None, payload=None) -> dict[str, Any]:
    request_url = url
    if params:
        clean_params = {key: value for key, value in params.items() if value is not None}
        encoded = parse.urlencode(clean_params, doseq=True)
        if encoded:
            request_url = f"{url}?{encoded}"
    body = None
    headers = build_headers(admin_token)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(request_url, data=body, headers=headers, method=method.upper())
    with request.urlopen(req, timeout=15.0) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def add_common_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--status", default="all")
    parser.add_argument("--company-code", default=None)


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", required=True, help="Base URL of license server, e.g. https://license.example.com")
    parser.add_argument("--admin-token", required=True, help="Admin token for X-Admin-Token header")


def command_summary(args: argparse.Namespace) -> int:
    payload = request_json(
        "GET",
        f"{normalize_base_url(args.base_url)}/admin/process-jobs/summary",
        admin_token=args.admin_token,
        params={
            "window_hours": args.window_hours,
            "status": args.status,
            "company_code": args.company_code,
        },
    )
    print_json(payload)
    return 0


def command_list(args: argparse.Namespace) -> int:
    payload = request_json(
        "GET",
        f"{normalize_base_url(args.base_url)}/admin/process-jobs",
        admin_token=args.admin_token,
        params={
            "window_hours": args.window_hours,
            "status": args.status,
            "company_code": args.company_code,
            "q": args.query,
            "limit": args.limit,
        },
    )
    print_json(payload)
    return 0


def command_check(args: argparse.Namespace) -> int:
    summary = request_json(
        "GET",
        f"{normalize_base_url(args.base_url)}/admin/process-jobs/summary",
        admin_token=args.admin_token,
        params={
            "window_hours": args.window_hours,
            "status": args.status,
            "company_code": args.company_code,
        },
    )
    print_json(summary)

    failed = int(summary.get("failed") or 0)
    stale = int(summary.get("stale") or 0)
    has_alerts = bool(summary.get("has_alerts"))
    failed_threshold = args.failed_threshold
    stale_threshold = args.stale_threshold

    if failed >= failed_threshold or stale >= stale_threshold or (args.fail_on_server_alert and has_alerts):
        if args.show_jobs:
            jobs = request_json(
                "GET",
                f"{normalize_base_url(args.base_url)}/admin/process-jobs",
                admin_token=args.admin_token,
                params={
                    "window_hours": args.window_hours,
                    "status": "all",
                    "company_code": args.company_code,
                    "limit": args.limit,
                },
            )
            print_json(jobs)
        return args.exit_code
    return 0


def command_cleanup(args: argparse.Namespace) -> int:
    payload = request_json(
        "POST",
        f"{normalize_base_url(args.base_url)}/admin/process-jobs/cleanup",
        admin_token=args.admin_token,
        payload={
            "retention_days": args.retention_days,
            "statuses": args.statuses,
            "limit": args.limit,
            "dry_run": args.dry_run,
        },
    )
    print_json(payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor and maintain license server process jobs")
    add_connection_args(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary_parser = subparsers.add_parser("summary", help="Fetch process job summary")
    add_common_filters(summary_parser)
    summary_parser.set_defaults(func=command_summary)

    list_parser = subparsers.add_parser("list", help="Fetch process jobs")
    add_common_filters(list_parser)
    list_parser.add_argument("--query", default=None)
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.set_defaults(func=command_list)

    check_parser = subparsers.add_parser("check", help="Return non-zero exit code on alert conditions")
    add_common_filters(check_parser)
    check_parser.add_argument("--failed-threshold", type=int, default=1)
    check_parser.add_argument("--stale-threshold", type=int, default=1)
    check_parser.add_argument("--fail-on-server-alert", action="store_true")
    check_parser.add_argument("--show-jobs", action="store_true")
    check_parser.add_argument("--limit", type=int, default=20)
    check_parser.add_argument("--exit-code", type=int, default=2)
    check_parser.set_defaults(func=command_check)

    cleanup_parser = subparsers.add_parser("cleanup", help="Delete or preview old process jobs")
    cleanup_parser.add_argument("--retention-days", type=int, default=30)
    cleanup_parser.add_argument("--statuses", nargs="*", default=None)
    cleanup_parser.add_argument("--limit", type=int, default=1000)
    cleanup_parser.add_argument("--dry-run", action="store_true")
    cleanup_parser.set_defaults(func=command_cleanup)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP error: {exc.code} {detail}", file=sys.stderr)
        return 1
    except error.URLError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
