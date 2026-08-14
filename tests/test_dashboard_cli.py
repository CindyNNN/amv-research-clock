from ai_invest_advisor.cli import build_parser


def test_dashboard_cli_commands_parse():
    parser = build_parser()

    refresh = parser.parse_args(["refresh-dashboard", "--offline"])
    report = parser.parse_args(["daily-report"])
    dashboard = parser.parse_args(["dashboard", "--port", "8601"])
    trade_advice = parser.parse_args(["trade-advice", "--top", "5", "--print"])

    assert refresh.command == "refresh-dashboard"
    assert refresh.offline is True
    assert report.command == "daily-report"
    assert dashboard.command == "dashboard"
    assert dashboard.port == 8601
    assert trade_advice.command == "trade-advice"
    assert trade_advice.top == 5
    assert trade_advice.print_report is True
