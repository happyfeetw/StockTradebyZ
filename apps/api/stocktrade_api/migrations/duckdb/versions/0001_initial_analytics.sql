CREATE TABLE IF NOT EXISTS market_daily_bars (
    trade_date DATE NOT NULL,
    code VARCHAR NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    turnover_n DOUBLE,
    source VARCHAR,
    imported_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (trade_date, code)
);

CREATE TABLE IF NOT EXISTS candidate_facts (
    candidate_id BIGINT,
    pick_date DATE NOT NULL,
    run_id VARCHAR NOT NULL,
    batch_id VARCHAR NOT NULL,
    code VARCHAR NOT NULL,
    strategy VARCHAR NOT NULL,
    close DOUBLE,
    turnover_n DOUBLE,
    brick_growth DOUBLE,
    extra_json JSON,
    created_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (batch_id, code, strategy)
);

CREATE TABLE IF NOT EXISTS review_facts (
    review_id BIGINT,
    review_run_id VARCHAR NOT NULL,
    pick_date DATE NOT NULL,
    run_id VARCHAR NOT NULL,
    code VARCHAR NOT NULL,
    strategy VARCHAR NOT NULL,
    review_key VARCHAR NOT NULL,
    verdict VARCHAR,
    total_score DOUBLE,
    payload_json JSON,
    created_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (review_run_id, review_key)
);

CREATE TABLE IF NOT EXISTS archive_facts (
    pick_date DATE NOT NULL,
    run_id VARCHAR NOT NULL,
    code VARCHAR NOT NULL,
    strategy VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    rank INTEGER,
    chart_artifact_id VARCHAR,
    payload_json JSON,
    created_at TIMESTAMP NOT NULL DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS strategy_run_metrics (
    pick_date DATE NOT NULL,
    run_id VARCHAR NOT NULL,
    strategy VARCHAR NOT NULL,
    total INTEGER NOT NULL DEFAULT 0,
    reviewed INTEGER NOT NULL DEFAULT 0,
    recommended INTEGER NOT NULL DEFAULT 0,
    unreviewed INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (pick_date, run_id, strategy)
);

CREATE TABLE IF NOT EXISTS analytics_snapshots (
    id VARCHAR PRIMARY KEY,
    kind VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
    params_json JSON,
    result_json JSON
);
