"""Execute transformed-layer catalogue specs via pure transformation functions."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Callable

import pandas as pd

from src.data import transformations as T
from src.data.variable_catalog import VariableSpec

if TYPE_CHECKING:
    from src.data.variable_catalog import VariableCatalog

TransformFn = Callable[..., pd.Series]

TRANSFORM_REGISTRY: dict[str, TransformFn] = {
    "rolling_zscore": T.rolling_zscore,
    "difference": T.difference,
    "yoy_pct_change": T.yoy_pct_change,
    "log_return": T.log_return,
    "rolling_vol": T.rolling_vol,
}


def _frequency_mismatch_message(
    spec: VariableSpec,
    source_name: str,
    source_freq: str,
) -> str:
    return (
        f"Transformation {spec.name!r} declares frequency={spec.spec.get('frequency')!r} but its "
        f"source {source_name!r} has native frequency={source_freq!r}. Implicit "
        f"resampling is not supported. To fix, declare an explicit intermediate "
        f"resample variable in transformations.yaml, e.g.:\n"
        f"  {source_name}_MONTHLY:\n"
        f"    layer: transformed\n"
        f"    source_variable: {source_name}\n"
        f"    transformation: resample\n"
        f"    method: last\n"
        f"    frequency: monthly\n"
        f"Then point {spec.name!r} at {source_name}_MONTHLY instead of {source_name!r}.\n"
        f"(Note: the `resample` transformation is not yet implemented; this is "
        f"documentation of the intended path. File an issue when first needed.)"
    )


def _spec_frequency(spec: VariableSpec) -> str:
    freq = spec.spec.get("frequency")
    if not isinstance(freq, str):
        raise ValueError(f"Transformation {spec.name!r} missing string 'frequency'.")
    return freq


def _source_frequency(catalogue: VariableCatalog, source_name: str) -> str:
    src_spec = catalogue.get_spec(source_name)
    freq = src_spec.spec.get("frequency")
    if not isinstance(freq, str):
        raise ValueError(f"Source {source_name!r} missing string 'frequency'.")
    return freq


def execute_transformation(
    spec: VariableSpec,
    catalogue: VariableCatalog,
    *,
    frequency: str | None,
    start: date | None,
    end: date | None,
    force_refresh: bool,
) -> pd.Series:
    """Execute a transformed-layer spec.

    See module docstring in the task specification for full behaviour.
    """
    if spec.layer != "transformed":
        raise ValueError(f"execute_transformation requires layer='transformed'; got {spec.layer!r}.")

    transformation = spec.spec.get("transformation")
    if not isinstance(transformation, str) or transformation not in TRANSFORM_REGISTRY:
        raise ValueError(
            f"Transformation {spec.name!r} has unknown transformation {transformation!r}. "
            f"Must be one of {sorted(TRANSFORM_REGISTRY)}."
        )

    spec_freq = _spec_frequency(spec)
    out_freq = frequency or spec_freq
    if frequency is not None and frequency != spec_freq:
        raise ValueError(
            f"Transformation {spec.name!r} declares frequency={spec_freq!r} but "
            f"frequency={frequency!r} was requested."
        )

    get_kw: dict[str, Any] = {
        "frequency": spec_freq,
        "start": start,
        "end": end,
        "force_refresh": force_refresh,
    }

    if transformation == "difference":
        sources = spec.spec.get("sources")
        if not isinstance(sources, list) or len(sources) != 2:
            raise ValueError(
                f"Transformation {spec.name!r}: difference requires sources: [lhs, rhs] with two names."
            )
        lhs_name, rhs_name = str(sources[0]), str(sources[1])
        lhs_freq = _source_frequency(catalogue, lhs_name)
        rhs_freq = _source_frequency(catalogue, rhs_name)
        if lhs_freq != rhs_freq:
            raise ValueError(
                f"Transformation {spec.name!r}: difference sources {lhs_name!r} ({lhs_freq}) "
                f"and {rhs_name!r} ({rhs_freq}) must share the same frequency."
            )
        if lhs_freq != spec_freq:
            raise ValueError(_frequency_mismatch_message(spec, lhs_name, lhs_freq))
        lhs = catalogue.get(lhs_name, **get_kw)
        rhs = catalogue.get(rhs_name, **get_kw)
        result = T.difference(lhs, rhs)
    else:
        source_variable = spec.spec.get("source_variable")
        if not isinstance(source_variable, str):
            raise ValueError(
                f"Transformation {spec.name!r} requires string 'source_variable'."
            )
        source_freq = _source_frequency(catalogue, source_variable)
        if source_freq != spec_freq:
            raise ValueError(
                _frequency_mismatch_message(spec, source_variable, source_freq)
            )
        source = catalogue.get(source_variable, **get_kw)
        result = _dispatch_single_input(transformation, source, spec)

    result = result.astype(float)
    result.name = spec.name
    return result


def _dispatch_single_input(
    transformation: str,
    source: pd.Series,
    spec: VariableSpec,
) -> pd.Series:
    s = spec.spec
    if transformation == "rolling_zscore":
        return T.rolling_zscore(source, window=int(s["window"]))
    if transformation == "yoy_pct_change":
        return T.yoy_pct_change(source, frequency=str(s["frequency"]))
    if transformation == "log_return":
        window = int(s.get("window", 1))
        return T.log_return(source, window=window)
    if transformation == "rolling_vol":
        return T.rolling_vol(
            source,
            window=int(s["window"]),
            annualised=bool(s.get("annualised", False)),
            frequency=str(s["frequency"]),
        )
    sources = s.get("sources")
    if isinstance(sources, list) and len(sources) > 1:
        raise NotImplementedError(
            f"Multi-input transformation {transformation!r} other than 'difference' "
            f"is not implemented."
        )
    raise ValueError(f"Unhandled transformation {transformation!r}.")
