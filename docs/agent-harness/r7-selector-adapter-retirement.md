# R7 Selector Adapter Retirement

Managing issue: #152
Parent epic: #23
Status date: 2026-05-28

The product preselect path no longer uses legacy selector compatibility
adapters as its default formula execution path. Product strategy selection uses
`ProductStrategyFormulaFactoryPort` with product-owned selector classes under
`src/stocktrade/domain/selection/selectors.py`.

## Scope

This retirement covers the default product preselect formula boundary:

- B1 formula preparation and `_vec_pick`;
- B2 formula preparation, B1 prior-signal lookup, quality-score fields, and
  `_vec_pick`;
- brick chart formula preparation, `brick_growth`, and `_vec_pick`;
- the default `LegacyPreselectExecutionPort.strategy_selectors` formula factory.

The legacy `pipeline/Selector.py` classes remain in the repository as a
compatibility and behavior-oracle surface for legacy CLI/parity work. They are
not the default product formula execution path.

## Product Replacement

Default product preselect wiring:

```text
PreselectService
  -> LegacyPreselectExecutionPort
  -> ProductStrategySelectorPort
  -> ProductStrategyFormulaFactoryPort
  -> ProductB1Selector / ProductB2Selector / ProductBrickChartSelector
```

The product selectors use product-owned indicator helpers from
`src/stocktrade/domain/selection/indicators.py`. They preserve the observable
selector columns consumed by the existing product strategy dispatcher:

- B1: `zxdq`, `zxdkx`, `K`, `D`, `J`, `wma_bull`, `_vec_pick`;
- B2: B1 columns plus `_b1_pick`, `_b2_prior_b1_lag`, `_b2_prior_b1_j`,
  `_b2_j_turn_up`, `_b2_daily_return`, `_b2_today_body_pct`,
  `_b2_volume_ratio`, `_b2_strict_yang_bao_yin`, `_b2_upper_shadow_ratio`,
  `_b2_quality_score`;
- brick: `zxdq`, `zxdkx`, `wma_bull`, `brick`, `brick_growth`, `_vec_pick`.

## Legacy Compatibility

`LegacyStrategyFormulaFactoryPort` remains available only for compatibility
tests, migration evidence, or rollback investigation. New product preselect
work must not make it the default formula factory again.

This change does not:

- alter strategy thresholds;
- change candidate identity or `(code, strategy)` semantics;
- remove `pipeline/Selector.py`;
- touch simulated or paper trading.

## Validation

Run:

```bash
PYTHONPATH=apps/api:src python3 -m unittest tests.test_preselect_domain_contracts
scripts/harness/check.sh r7-selector-adapter-retirement
scripts/harness/check.sh r7-retirement-plan
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh quick
```

The `r7-selector-adapter-retirement` gate proves:

- the product selector module exists and owns B1/B2/brick selector classes;
- default preselect wiring uses `ProductStrategyFormulaFactoryPort`;
- the legacy formula factory remains explicit compatibility only;
- product selector preparation matches the legacy selector preparation columns
  under parity tests;
- simulated trading remains out of scope.

## Rollback

Rollback is to explicitly inject `LegacyStrategyFormulaFactoryPort` into
`ProductStrategySelectorPort` for a parity or incident run. Do not change the
default product wiring without a new #152 PR and replacement evidence.
