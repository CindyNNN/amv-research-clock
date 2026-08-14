from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from ai_invest_advisor import __version__
from ai_invest_advisor.config import load_settings
from ai_invest_advisor.data.tech_board_downloader import download_tech_board_data
from ai_invest_advisor.data.tech_universe import TECH_THEME_RULES
from ai_invest_advisor.dashboard.pipeline import DASHBOARD_CACHE_DIR, refresh_dashboard_cache
from ai_invest_advisor.dashboard.report import generate_daily_report
from ai_invest_advisor.dashboard.trade_advice import generate_trade_advice_report


def print_version(_: argparse.Namespace) -> None:
    print(f"ai-invest-advisor {__version__}")


def print_tech_themes(_: argparse.Namespace) -> None:
    print("Technology Board Universe")
    print("-" * 80)
    for rule in TECH_THEME_RULES:
        print(f"{rule.theme}: {' / '.join(rule.keywords)}")


def download_tech_boards(args: argparse.Namespace) -> None:
    settings = load_settings(args.settings_path)
    result = download_tech_board_data(
        settings.tech_boards,
        include_history=not args.no_history,
        include_constituents=not args.no_constituents,
    )

    print(f"Downloaded tech board data to: {result.output_dir}")
    print(f"Matched concept boards: {result.concept_count}")
    print(f"Matched industry boards: {result.industry_count}")
    print(f"CSV files written: {len(result.files)}")
    if result.failures:
        print(f"Partial failures: {len(result.failures)}. See download_failures.csv")


def refresh_dashboard(args: argparse.Namespace) -> None:
    settings = load_settings(args.settings_path)
    result = refresh_dashboard_cache(settings=settings, allow_network=not args.offline)
    print(f"Dashboard cache: {result.cache_dir}")
    print(f"Market heat: {result.market_heat.score:.2f} ({result.market_heat.label})")
    print(f"Tech boards scored: {result.row_count}")
    print(f"Data status: {result.status}")


def daily_report(args: argparse.Namespace) -> None:
    path = generate_daily_report(cache_dir=args.cache_dir, report_dir=args.report_dir, report_date=args.report_date)
    print(f"Daily report written: {path}")


def dashboard(args: argparse.Namespace) -> None:
    app_path = Path(__file__).parent / "dashboard" / "app.py"
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        str(args.port),
    ]
    print(f"Starting dashboard at http://localhost:{args.port}")
    subprocess.run(command, check=False)


def trade_advice(args: argparse.Namespace) -> None:
    try:
        path = generate_trade_advice_report(cache_dir=args.cache_dir, output_dir=args.output_dir, top=args.top)
    except FileNotFoundError as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    print(f"Trade advice report written: {path}")
    if args.print_report:
        print(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local A/H share AI investment research assistant.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    version_parser = subparsers.add_parser("version", help="Show package version.")
    version_parser.set_defaults(func=print_version)

    themes_parser = subparsers.add_parser("tech-themes", help="List configured technology board themes.")
    themes_parser.set_defaults(func=print_tech_themes)

    download_parser = subparsers.add_parser("download-tech-boards", help="Download technology board data from AKShare.")
    download_parser.add_argument(
        "--settings-path",
        type=Path,
        default=Path("config/settings.toml"),
        help="Settings file path.",
    )
    download_parser.add_argument(
        "--no-history",
        action="store_true",
        help="Skip board K-line history download.",
    )
    download_parser.add_argument(
        "--no-constituents",
        action="store_true",
        help="Skip board constituents download.",
    )
    download_parser.set_defaults(func=download_tech_boards)

    refresh_parser = subparsers.add_parser("refresh-dashboard", help="Refresh dashboard data cache.")
    refresh_parser.add_argument(
        "--settings-path",
        type=Path,
        default=Path("config/settings.toml"),
        help="Settings file path.",
    )
    refresh_parser.add_argument(
        "--offline",
        action="store_true",
        help="Use cached market flow instead of fetching live data.",
    )
    refresh_parser.set_defaults(func=refresh_dashboard)

    report_parser = subparsers.add_parser("daily-report", help="Generate a Markdown daily dashboard report.")
    report_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DASHBOARD_CACHE_DIR,
        help="Dashboard cache directory.",
    )
    report_parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/daily"),
        help="Daily report output directory.",
    )
    report_parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Optional report date override.",
    )
    report_parser.set_defaults(func=daily_report)

    dashboard_parser = subparsers.add_parser("dashboard", help="Start the local Streamlit dashboard.")
    dashboard_parser.add_argument(
        "--port",
        type=int,
        default=8501,
        help="Local Streamlit port.",
    )
    dashboard_parser.set_defaults(func=dashboard)

    trade_parser = subparsers.add_parser("trade-advice", help="Generate a technology-board trading advice report.")
    trade_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DASHBOARD_CACHE_DIR,
        help="Dashboard cache directory.",
    )
    trade_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/trade_advice"),
        help="Trade advice report output directory.",
    )
    trade_parser.add_argument(
        "--top",
        type=int,
        default=8,
        help="Number of boards to include per action bucket.",
    )
    trade_parser.add_argument(
        "--print",
        dest="print_report",
        action="store_true",
        help="Print the generated Markdown report after writing it.",
    )
    trade_parser.set_defaults(func=trade_advice)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
