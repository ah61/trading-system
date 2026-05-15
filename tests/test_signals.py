from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd
import pytest

from src.signals.base import Signal


def test_signal_cannot_instantiate_directly() -> None:
    with pytest.raises(TypeError):
        _ = Signal()  # type: ignore[abstract]


class _ConcreteSignal(Signal):
    name = "test_signal"
    asset_class = "fx"
    signal_type = "momentum"
    frequency = "daily"
    params = {"window": 5}
    required_variables = ["EURUSD"]

    def compute(self, data: Dict[str, pd.Series]) -> pd.Series:
        s = data["EURUSD"].astype(float)
        out = s.diff()
        out.index = pd.to_datetime(out.index, utc=True)
        return out


def test_normalise_zscore_output_within_bounds() -> None:
    sig = _ConcreteSignal()
    idx = pd.date_range("2020-01-01", periods=400, freq="B", tz="UTC")
    raw = pd.Series(np.random.default_rng(0).normal(size=len(idx)), index=idx)

    norm = sig.normalise(raw, method="zscore", window=252).dropna()
    assert (norm >= -1.0).all()
    assert (norm <= 1.0).all()


def test_normalise_rank_output_within_bounds() -> None:
    sig = _ConcreteSignal()
    idx = pd.date_range("2024-01-01", periods=50, freq="B", tz="UTC")
    raw = pd.Series(np.linspace(-5.0, 5.0, num=len(idx)), index=idx)

    norm = sig.normalise(raw, method="rank")
    assert (norm >= -1.0).all()
    assert (norm <= 1.0).all()


def test_get_metadata_returns_required_keys() -> None:
    sig = _ConcreteSignal()
    meta = sig.get_metadata()
    assert set(meta.keys()) == {
        "name",
        "asset_class",
        "signal_type",
        "frequency",
        "params",
        "required_variables",
    }


def test_normalise_unknown_method_raises() -> None:
    sig = _ConcreteSignal()
    idx = pd.date_range("2024-01-01", periods=10, freq="B", tz="UTC")
    raw = pd.Series(np.arange(len(idx), dtype=float), index=idx)
    with pytest.raises(ValueError):
        _ = sig.normalise(raw, method="nope")


# ---------------------------------------------------------------------------
# FX Carry
# ---------------------------------------------------------------------------


def _fx_carry_cfg(rate_series: dict[str, str], limitations: list[str] | None = None) -> dict:
    """Helper: build a complete fx_carry config dict for monkeypatching."""
    return {
        "signal": {
            "name": "fx_carry",
            "asset_class": "fx",
            "signal_type": "carry",
            "frequency": "monthly",
        },
        "parameters": {
            "base_currency": "USD",
            "rate_series": rate_series,
            "lookback_smooth": 1,
            "n_long": 2,
            "n_short": 2,
        },
        "known_limitations": list(limitations or []),
    }


def test_fx_carry_compute_returns_series(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.fx.carry import FXCarrySignal

    cfg = _fx_carry_cfg(
        rate_series={"USD": "DFF", "EUR": "EUR_RATE", "GBP": "GBP_RATE"},
        limitations=["a", "b"],
    )
    monkeypatch.setattr(FXCarrySignal, "_load_config", classmethod(lambda cls, _: cfg))

    idx = pd.date_range("2024-01-01", periods=6, freq="B", tz="UTC")
    data: Dict[str, pd.Series] = {
        "DFF": pd.Series(np.linspace(5.0, 5.5, len(idx), dtype=np.float64), index=idx),
        "EUR_RATE": pd.Series(np.linspace(3.0, 3.2, len(idx), dtype=np.float64), index=idx),
        "GBP_RATE": pd.Series(np.linspace(4.0, 4.1, len(idx), dtype=np.float64), index=idx),
    }

    sig = FXCarrySignal()
    out = sig.compute(data)

    assert isinstance(out, pd.Series)
    assert out.dtype == float
    assert out.index.nlevels == 2
    assert (out.dropna() >= -1.0).all()
    assert (out.dropna() <= 1.0).all()


def test_fx_carry_required_variables_derived_from_rate_series(monkeypatch: pytest.MonkeyPatch) -> None:
    """The signal's required_variables list is derived from rate_series RHS,
    sorted and deduplicated. Verifies the 5.7 catalogue contract."""
    from src.signals.fx.carry import FXCarrySignal

    cfg = _fx_carry_cfg(rate_series={"USD": "DFF", "EUR": "EUR_RATE", "GBP": "GBP_RATE"})
    monkeypatch.setattr(FXCarrySignal, "_load_config", classmethod(lambda cls, _: cfg))

    sig = FXCarrySignal()
    assert sig.required_variables == ["DFF", "EUR_RATE", "GBP_RATE"]


def test_fx_carry_no_lookahead(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.fx.carry import FXCarrySignal

    cfg = _fx_carry_cfg(
        rate_series={"USD": "DFF", "EUR": "EUR_RATE", "GBP": "GBP_RATE"},
        limitations=["a"],
    )
    monkeypatch.setattr(FXCarrySignal, "_load_config", classmethod(lambda cls, _: cfg))

    idx = pd.date_range("2024-01-01", periods=8, freq="B", tz="UTC")
    base: Dict[str, pd.Series] = {
        "DFF": pd.Series(np.linspace(5.0, 5.7, len(idx), dtype=np.float64), index=idx),
        "EUR_RATE": pd.Series(np.linspace(3.0, 3.1, len(idx), dtype=np.float64), index=idx),
        "GBP_RATE": pd.Series(np.linspace(4.0, 4.3, len(idx), dtype=np.float64), index=idx),
    }
    perturbed = {k: v.copy() for k, v in base.items()}

    t = idx[4]
    t_plus_1 = idx[5]
    perturbed["EUR_RATE"].loc[t_plus_1] += np.float64(10.0)

    sig = FXCarrySignal()
    out_base = sig.compute(base)
    out_perturbed = sig.compute(perturbed)

    assert out_base.xs(t, level=0).equals(out_perturbed.xs(t, level=0))


def test_fx_carry_metadata_has_limitations(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.fx.carry import FXCarrySignal

    limitations = [
        "Rate differential proxies forward premium — approximation only",
        "Does not include actual FX rollover costs",
    ]
    cfg = _fx_carry_cfg(
        rate_series={"USD": "DFF", "EUR": "EUR_RATE"},
        limitations=limitations,
    )
    monkeypatch.setattr(FXCarrySignal, "_load_config", classmethod(lambda cls, _: cfg))

    sig = FXCarrySignal()
    meta = sig.get_metadata()
    assert "known_limitations" in meta
    assert meta["known_limitations"] == limitations


# ---------------------------------------------------------------------------
# Rates Trend
# ---------------------------------------------------------------------------


def _rates_trend_cfg(variable: str = "TLT_CLOSE", **overrides) -> dict:
    """Helper: build a rates_trend config dict for monkeypatching."""
    params = {
        "variable": variable,
        "fast_window": 50,
        "slow_window": 200,
        "scale_by_distance": False,
    }
    params.update(overrides)
    return {
        "signal": {
            "name": "rates_trend",
            "asset_class": "rates",
            "signal_type": "trend",
            "frequency": "daily",
        },
        "parameters": params,
    }


def test_rates_trend_compute_returns_series(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.rates.trend import RatesTrendSignal

    cfg = _rates_trend_cfg()
    monkeypatch.setattr(RatesTrendSignal, "_load_config", classmethod(lambda cls, _: cfg))

    idx = pd.date_range("2024-01-01", periods=300, freq="B", tz="UTC")
    close = np.linspace(100.0, 120.0, len(idx), dtype=np.float64)
    data: Dict[str, pd.Series] = {"TLT_CLOSE": pd.Series(close, index=idx)}

    sig = RatesTrendSignal()
    out = sig.compute(data).dropna()

    assert isinstance(out, pd.Series)
    assert out.index.tz is not None
    assert str(out.index.tz) in ("UTC", "UTC+00:00")
    assert (out >= -1.0).all()
    assert (out <= 1.0).all()


def test_rates_trend_required_variables_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The variable parameter populates required_variables as a single-entry list."""
    from src.signals.rates.trend import RatesTrendSignal

    cfg = _rates_trend_cfg(variable="TLT_CLOSE")
    monkeypatch.setattr(RatesTrendSignal, "_load_config", classmethod(lambda cls, _: cfg))

    sig = RatesTrendSignal()
    assert sig.required_variables == ["TLT_CLOSE"]


def test_rates_trend_no_lookahead(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.rates.trend import RatesTrendSignal

    cfg = _rates_trend_cfg(scale_by_distance=True)
    monkeypatch.setattr(RatesTrendSignal, "_load_config", classmethod(lambda cls, _: cfg))

    idx = pd.date_range("2024-01-01", periods=320, freq="B", tz="UTC")
    close = np.linspace(100.0, 130.0, len(idx), dtype=np.float64)
    base: Dict[str, pd.Series] = {"TLT_CLOSE": pd.Series(close, index=idx)}
    perturbed: Dict[str, pd.Series] = {"TLT_CLOSE": base["TLT_CLOSE"].copy()}

    t = idx[250]
    t_plus_1 = idx[251]
    perturbed["TLT_CLOSE"].loc[t_plus_1] += np.float64(500.0)

    sig = RatesTrendSignal()
    out_base = sig.compute(base)
    out_perturbed = sig.compute(perturbed)

    assert float(out_base.loc[t]) == float(out_perturbed.loc[t])


def test_rates_trend_signal_negative_in_downtrend(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.rates.trend import RatesTrendSignal

    cfg = _rates_trend_cfg()
    monkeypatch.setattr(RatesTrendSignal, "_load_config", classmethod(lambda cls, _: cfg))

    idx = pd.date_range("2024-01-01", periods=300, freq="B", tz="UTC")
    close = np.linspace(120.0, 100.0, len(idx), dtype=np.float64)
    data: Dict[str, pd.Series] = {"TLT_CLOSE": pd.Series(close, index=idx)}

    sig = RatesTrendSignal()
    out = sig.compute(data).dropna()
    assert float(out.iloc[-1]) < 0.0


def test_rates_trend_signal_positive_in_uptrend(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.rates.trend import RatesTrendSignal

    cfg = _rates_trend_cfg()
    monkeypatch.setattr(RatesTrendSignal, "_load_config", classmethod(lambda cls, _: cfg))

    idx = pd.date_range("2024-01-01", periods=300, freq="B", tz="UTC")
    close = np.linspace(100.0, 120.0, len(idx), dtype=np.float64)
    data: Dict[str, pd.Series] = {"TLT_CLOSE": pd.Series(close, index=idx)}

    sig = RatesTrendSignal()
    out = sig.compute(data).dropna()
    assert float(out.iloc[-1]) > 0.0


# ---------------------------------------------------------------------------
# Equity Momentum
# ---------------------------------------------------------------------------


def _equity_momentum_cfg() -> dict:
    return {
        "signal": {
            "name": "equity_momentum",
            "asset_class": "equities",
            "signal_type": "momentum",
            "frequency": "monthly",
        },
        "parameters": {
            "formation_months": 12,
            "skip_months": 1,
            "universe": "sp500_current",
            "rebalance_freq": "monthly",
        },
    }


def _patch_universe(
    monkeypatch: pytest.MonkeyPatch,
    tickers: list[str],
    pattern: str = "{ticker}_CLOSE",
) -> list[str]:
    """Monkeypatch the universe loader and return the expanded variable names."""
    from src.signals.equities.momentum import EquityMomentumSignal

    monkeypatch.setattr(
        EquityMomentumSignal,
        "_load_universe",
        classmethod(lambda cls, _: (list(tickers), pattern)),
    )
    return [pattern.format(ticker=t) for t in tickers]


def test_equity_momentum_compute_returns_series(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.equities.momentum import EquityMomentumSignal

    tickers = [f"T{i}" for i in range(10)]
    cfg = _equity_momentum_cfg()
    monkeypatch.setattr(EquityMomentumSignal, "_load_config", classmethod(lambda cls, _: cfg))
    variables = _patch_universe(monkeypatch, tickers)

    idx = pd.date_range("2022-01-03", periods=600, freq="B", tz="UTC")
    data: Dict[str, pd.Series] = {}
    for i, var in enumerate(variables):
        close = np.linspace(100.0, 150.0 + i, len(idx), dtype=np.float64)
        data[var] = pd.Series(close, index=idx)

    sig = EquityMomentumSignal()
    out = sig.compute(data).dropna()

    assert isinstance(out, pd.Series)
    assert out.index.nlevels == 2
    assert (out >= -1.0).all()
    assert (out <= 1.0).all()


def test_equity_momentum_required_variables_use_template_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catalogue variable names come from applying variable_name_pattern to tickers."""
    from src.signals.equities.momentum import EquityMomentumSignal

    tickers = ["AAPL", "MSFT", "JPM"]
    cfg = _equity_momentum_cfg()
    monkeypatch.setattr(EquityMomentumSignal, "_load_config", classmethod(lambda cls, _: cfg))
    _patch_universe(monkeypatch, tickers, pattern="{ticker}_CLOSE")

    sig = EquityMomentumSignal()
    assert sig.required_variables == ["AAPL_CLOSE", "MSFT_CLOSE", "JPM_CLOSE"]


def test_equity_momentum_custom_name_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    """A universe with a different variable_name_pattern produces matching names."""
    from src.signals.equities.momentum import EquityMomentumSignal

    tickers = ["AAPL", "MSFT"]
    cfg = _equity_momentum_cfg()
    monkeypatch.setattr(EquityMomentumSignal, "_load_config", classmethod(lambda cls, _: cfg))
    _patch_universe(monkeypatch, tickers, pattern="{ticker}_PX")

    sig = EquityMomentumSignal()
    assert sig.required_variables == ["AAPL_PX", "MSFT_PX"]


def test_equity_momentum_no_lookahead(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.equities.momentum import EquityMomentumSignal

    tickers = [f"T{i}" for i in range(10)]
    cfg = _equity_momentum_cfg()
    monkeypatch.setattr(EquityMomentumSignal, "_load_config", classmethod(lambda cls, _: cfg))
    variables = _patch_universe(monkeypatch, tickers)

    idx = pd.date_range("2022-01-03", periods=650, freq="B", tz="UTC")
    base: Dict[str, pd.Series] = {}
    for i, var in enumerate(variables):
        close = np.linspace(100.0, 160.0 + i, len(idx), dtype=np.float64)
        base[var] = pd.Series(close, index=idx)

    perturbed = {k: v.copy() for k, v in base.items()}

    sig = EquityMomentumSignal()
    out_base = sig.compute(base)

    # Choose a rebalance date and perturb the next business day (t+1) for one stock.
    t = out_base.index.get_level_values(0).unique().sort_values()[15]
    t_plus_1 = pd.Timestamp((t + pd.tseries.offsets.BDay(1)).to_pydatetime())
    perturbed[variables[0]].loc[t_plus_1] += np.float64(9999.0)

    out_perturbed = sig.compute(perturbed)
    assert out_base.xs(t, level=0).equals(out_perturbed.xs(t, level=0))


def test_equity_momentum_metadata_survivorship_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.equities.momentum import EquityMomentumSignal

    cfg = _equity_momentum_cfg()
    monkeypatch.setattr(EquityMomentumSignal, "_load_config", classmethod(lambda cls, _: cfg))
    _patch_universe(monkeypatch, ["A", "B"])

    sig = EquityMomentumSignal()
    meta = sig.get_metadata()
    assert meta.get("survivorship_biased") is True


def test_equity_momentum_winners_positive_losers_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.equities.momentum import EquityMomentumSignal

    tickers = ["A", "B"] + [f"X{i}" for i in range(8)]
    cfg = _equity_momentum_cfg()
    monkeypatch.setattr(EquityMomentumSignal, "_load_config", classmethod(lambda cls, _: cfg))
    variables = _patch_universe(monkeypatch, tickers)

    idx = pd.date_range("2022-01-03", periods=650, freq="B", tz="UTC")
    data: Dict[str, pd.Series] = {}

    # A clearly outperforms; B clearly underperforms; others roughly flat.
    data["A_CLOSE"] = pd.Series(np.linspace(100.0, 250.0, len(idx), dtype=np.float64), index=idx)
    data["B_CLOSE"] = pd.Series(np.linspace(200.0, 80.0, len(idx), dtype=np.float64), index=idx)
    for var in variables[2:]:
        data[var] = pd.Series(np.linspace(100.0, 105.0, len(idx), dtype=np.float64), index=idx)

    sig = EquityMomentumSignal()
    out = sig.compute(data).dropna()

    last_date = out.index.get_level_values(0).unique().sort_values()[-1]
    cs = out.xs(last_date, level=0)
    # Asset level is the catalogue variable name (e.g. "A_CLOSE"), not the ticker.
    assert float(cs.loc["A_CLOSE"]) > 0.0
    assert float(cs.loc["B_CLOSE"]) < 0.0
