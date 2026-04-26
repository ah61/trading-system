"""
Phase 0 Setup Script — trading-system
Run this from the project root on Windows after cloning the repo.
Usage: python scripts/setup_env.py
"""

import os
import sys
import subprocess
from pathlib import Path


def run(cmd: str, check=True):
    print(f"\n>>> {cmd}")
    result = subprocess.run(cmd, shell=True, check=check)
    return result


def create_dir_structure():
    dirs = [
        "docs",
        "configs/data",
        "configs/signals",
        "configs/portfolio",
        "data/raw",
        "data/adjusted",
        "data/derived",
        "src/data/sources",
        "src/signals/fx",
        "src/signals/rates",
        "src/signals/equities",
        "src/evaluation",
        "src/portfolio",
        "src/backtest",
        "tests/test_data",
        "notebooks",
        "reports",
        "scripts",
    ]
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
        init = Path(d) / "__init__.py"
        if d.startswith("src") and not init.exists():
            init.touch()
    print("Directory structure created.")


def create_env_example():
    content = """# .env.example — copy to .env and fill in your keys
# Never commit .env to git

FRED_API_KEY=your_fred_api_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here
IB_HOST=127.0.0.1
IB_PORT=7497
IB_CLIENT_ID=1
QUANDL_API_KEY=your_quandl_api_key_here
"""
    Path(".env.example").write_text(content)
    if not Path(".env").exists():
        Path(".env").write_text(content.replace("your_", "REPLACE_").replace("_here", ""))
        print(".env.example and .env created. Fill in .env with your actual keys.")


def create_gitignore():
    content = """.env
data/raw/
data/adjusted/
data/derived/
reports/
*.pyc
__pycache__/
.venv/
*.egg-info/
.pytest_cache/
notebooks/.ipynb_checkpoints/
*.duckdb
*.db
.DS_Store
Thumbs.db
"""
    Path(".gitignore").write_text(content)
    print(".gitignore created.")


def create_requirements():
    req = """pandas>=2.0
numpy>=1.24
scipy>=1.10
duckdb>=0.9
fredapi
yfinance
ib_insync
pyfolio-reloaded
statsmodels
matplotlib
seaborn
python-dotenv
pyyaml
loguru
"""
    req_dev = """pytest
pytest-cov
black
ruff
ipykernel
jupyter
"""
    Path("requirements.txt").write_text(req)
    Path("requirements-dev.txt").write_text(req_dev)
    print("requirements.txt and requirements-dev.txt created.")


def create_exceptions():
    content = '''"""
src/exceptions.py
Custom exception hierarchy for the trading system.
All exceptions inherit from TradingSystemError for easy catch-all handling.
"""


class TradingSystemError(Exception):
    """Base exception for all trading system errors."""
    pass


class DataFetchError(TradingSystemError):
    """Raised when a data source fails to return data."""
    pass


class DataGapError(TradingSystemError):
    """Raised when missing data exceeds the allowable fill threshold."""
    pass


class DataValidationError(TradingSystemError):
    """Raised when data fails schema or content validation."""
    pass


class LookaheadError(TradingSystemError):
    """Raised when a lookahead bias violation is detected."""
    pass


class SignalComputationError(TradingSystemError):
    """Raised when signal computation fails."""
    pass


class InsufficientDataError(TradingSystemError):
    """Raised when there is insufficient data for a computation."""
    pass


class ConfigError(TradingSystemError):
    """Raised when configuration is missing or invalid."""
    pass


class StorageError(TradingSystemError):
    """Raised when data storage operations fail."""
    pass
'''
    Path("src/exceptions.py").write_text(content)
    print("src/exceptions.py created.")


def create_readme():
    content = """# Systematic Multi-Asset Trading System

A modular, multi-signal trading system spanning FX, Rates, and Equities.
Built for rigorous backtesting, overfitting correction, and eventual live deployment.

## Architecture

See `docs/ARCHITECTURE.md` for full system design.

## Asset Classes

- **FX:** G10 carry and momentum
- **Rates:** Trend following on duration (ETF proxies → futures)
- **Equities:** Cross-sectional momentum

## Validation Framework

- Walk-forward and CPCV backtesting
- Deflated Sharpe Ratio (Bailey & López de Prado 2014)
- Probability of Backtest Overfitting
- Hansen's SPA test

## Setup

```bash
python -m venv .venv
.venv\\Scripts\\activate      # Windows
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp .env.example .env         # Fill in your API keys
```

## Build Status

| Phase | Status |
|-------|--------|
| 0. Environment | 🔄 In Progress |
| 1. Data Pipeline | ⬜ Not Started |
| 2. Signal Engine | ⬜ Not Started |
| 3. Portfolio Engine | ⬜ Not Started |
| 4. Backtest Engine | ⬜ Not Started |
| 5. Paper Trading | ⬜ Not Started |
"""
    Path("README.md").write_text(content)
    print("README.md created.")


def main():
    print("=== Trading System — Phase 0 Setup ===\n")

    create_dir_structure()
    create_env_example()
    create_gitignore()
    create_requirements()
    create_exceptions()
    create_readme()

    print("\n=== Creating virtual environment ===")
    run("python -m venv .venv")

    print("\n=== Installing dependencies ===")
    run(r".venv\Scripts\pip install -r requirements.txt")
    run(r".venv\Scripts\pip install -r requirements-dev.txt")

    print("\n=== Verifying setup ===")
    run(r".venv\Scripts\python -m pytest tests/ --collect-only", check=False)
    run(r".venv\Scripts\black --check src/", check=False)

    print("""
=== Phase 0 Complete ===

Next steps:
1. Fill in your API keys in .env
2. Open this folder in Cursor
3. Set Cursor model to claude-sonnet via Anthropic API key
4. Read ROADMAP.md Phase 1 and begin Milestone 1.1 (DataStore)

Git initialisation (run manually):
  git init
  git add .
  git commit -m "feat: initial project structure (Phase 0)"
""")


if __name__ == "__main__":
    main()
