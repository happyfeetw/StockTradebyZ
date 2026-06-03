# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python-based A-share semi-automated stock selection system. The main workflow starts in `run_all.py` and chains data fetching, quantitative preselection, chart export, Gemini review, and recommendation output.

- `pipeline/`: core data fetching, selection strategies, candidate schemas, CLI, and result archiving.
- `agent/`: Gemini CLI/API review logic, scoring normalization, and prompt template.
- `dashboard/`: Streamlit dashboard and K-line chart export tools.
- `workbench/`: local Streamlit workbench entry point.
- `paper_trading/`: paper-trading subsystem.
- `config/`: YAML runtime configuration.
- `tests/`: unit tests for strategies and review contracts.
- `data/`: local runtime outputs; ignored by git.

## Build, Test, and Development Commands

```bash
pip install -r requirements.txt
python run_all.py
python run_all.py --skip-fetch --skip-review
python -m pipeline.fetch_kline
python -m pipeline.cli preselect
python dashboard/export_kline_charts.py
python agent/gemini_cli_review.py
python -m pipeline.archive_results
python -m pytest tests/ -v
bash start_workbench
```

Use `run_all.py` for the full pipeline. Use the module-level commands when validating one stage. `bash start_workbench` launches the local workbench; direct fallback is `streamlit run workbench/app.py --server.port 8601`.

## Coding Style & Naming Conventions

Use Python 3 style with 4-space indentation, explicit imports, and clear snake_case names for modules, functions, variables, and config keys. Prefer `Path` over string path joins when touching filesystem code. Keep strategy and review contracts stable: candidate data should preserve `code`, `strategy`, and review identifiers. No central formatter is configured, so match the surrounding file style and keep patches minimal.

## Testing Guidelines

Tests are `unittest`-compatible and are normally run through pytest:

```bash
python -m pytest tests/ -v
python -m pytest tests/test_b2_strategy.py -v
python -m pytest tests/test_review_contracts.py -v
```

Name new test files `tests/test_*.py` and test methods `test_*`. Add focused tests for strategy filters, scoring normalization, archive compatibility, and JSON contract changes.

## Commit & Pull Request Guidelines

Recent history uses short imperative subjects such as `Add classic pattern review scoring` and `Refine brick trigger review window`. Keep commits scoped to one behavior or document change. Pull requests should explain the user-visible impact, list validation commands, link any related issue, and include screenshots for Streamlit UI changes.

## Security & Configuration Tips

Never commit secrets, generated certificates, raw market data, local logs, or `.env` files. `TUSHARE_TOKEN` is required for data fetching. Gemini CLI review expects local Gemini CLI login; legacy API review additionally needs `GEMINI_API_KEY`. Runtime outputs under `data/` are local artifacts and should stay out of git.
