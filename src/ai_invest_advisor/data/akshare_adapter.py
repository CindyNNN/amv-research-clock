from __future__ import annotations

from datetime import date

import pandas as pd


def _akshare_module():
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("AKShare is not installed. Run: uv pip install -e \".[data,test]\"") from exc
    return ak


def fetch_concept_boards() -> pd.DataFrame:
    ak = _akshare_module()
    return ak.stock_board_concept_name_em()


def fetch_industry_boards() -> pd.DataFrame:
    ak = _akshare_module()
    return ak.stock_board_industry_name_em()


def fetch_concept_fund_flow(symbol: str = "即时") -> pd.DataFrame:
    ak = _akshare_module()
    return ak.stock_fund_flow_concept(symbol=symbol)


def fetch_industry_fund_flow(symbol: str = "即时") -> pd.DataFrame:
    ak = _akshare_module()
    return ak.stock_fund_flow_industry(symbol=symbol)


def fetch_concept_boards_ths() -> pd.DataFrame:
    ak = _akshare_module()
    return ak.stock_board_concept_name_ths()


def fetch_industry_boards_ths() -> pd.DataFrame:
    ak = _akshare_module()
    return ak.stock_board_industry_name_ths()


def fetch_concept_constituents(board_name: str) -> pd.DataFrame:
    ak = _akshare_module()
    return ak.stock_board_concept_cons_em(symbol=board_name)


def fetch_industry_constituents(board_name: str) -> pd.DataFrame:
    ak = _akshare_module()
    return ak.stock_board_industry_cons_em(symbol=board_name)


def fetch_concept_history(board_name: str, start_date: str, end_date: str | None = None, period: str = "daily", adjust: str = "") -> pd.DataFrame:
    ak = _akshare_module()
    end = end_date or date.today().strftime("%Y%m%d")
    return ak.stock_board_concept_hist_em(symbol=board_name, period=period, start_date=start_date, end_date=end, adjust=adjust)


def fetch_industry_history(board_name: str, start_date: str, end_date: str | None = None, period: str = "daily", adjust: str = "") -> pd.DataFrame:
    ak = _akshare_module()
    end = end_date or date.today().strftime("%Y%m%d")
    return ak.stock_board_industry_hist_em(symbol=board_name, period=period, start_date=start_date, end_date=end, adjust=adjust)


def fetch_concept_info_ths(board_name: str) -> pd.DataFrame:
    ak = _akshare_module()
    return ak.stock_board_concept_info_ths(symbol=board_name)


def fetch_industry_info_ths(board_name: str) -> pd.DataFrame:
    ak = _akshare_module()
    return ak.stock_board_industry_info_ths(symbol=board_name)


def fetch_concept_history_ths(board_name: str, start_date: str, end_date: str | None = None) -> pd.DataFrame:
    ak = _akshare_module()
    end = end_date or date.today().strftime("%Y%m%d")
    return ak.stock_board_concept_index_ths(symbol=board_name, start_date=start_date, end_date=end)


def fetch_industry_history_ths(board_name: str, start_date: str, end_date: str | None = None) -> pd.DataFrame:
    ak = _akshare_module()
    end = end_date or date.today().strftime("%Y%m%d")
    return ak.stock_board_industry_index_ths(symbol=board_name, start_date=start_date, end_date=end)
