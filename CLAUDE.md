# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

AgentTrader：A 股半自动选股系统。通过 Tushare 拉取日线数据 → 量化初选（B1/B2/Brick 策略）→ 导出 K 线图 → Gemini CLI 对图表进行 AI 复评打分 → 输出推荐列表。

## 常用命令

```bash
# 一键全流程
python run_all.py
python run_all.py --skip-fetch              # 跳过数据下载
python run_all.py --start-from 3            # 从第 N 步开始
python run_all.py --skip-review             # 跳过复评

# 分步运行
python -m pipeline.fetch_kline              # 步骤1: 拉取K线
python -m pipeline.cli preselect             # 步骤2: 量化初选
python dashboard/export_kline_charts.py     # 步骤3: 导出K线图
python agent/gemini_cli_review.py           # 步骤4: Gemini CLI复评
python -m pipeline.archive_results          # 归档当日结果

# 测试
python -m pytest tests/ -v
python -m pytest tests/test_b2_strategy.py -v
python -m pytest tests/test_review_contracts.py -v

# 启动本地工作台
bash start_workbench
# 或直接: streamlit run workbench/app.py --server.port 8601
```

## 架构

```
pipeline/         数据抓取与量化初选（核心计算层，纯 Python）
  fetch_kline.py      Tushare 数据下载（多线程）
  pipeline_core.py    MarketDataPreparer（多进程预处理）、TopTurnoverPoolBuilder、SelectorPickPrecomputer
  select_stock.py     run_b1 / run_b2 / run_brick 策略入口 + run_preselect 主函数
  Selector.py         B1Selector、B2Selector、BrickChartSelector 信号逻辑（263KB，最重的文件）
  schemas.py          Candidate / CandidateRun dataclass（纯数据结构，无依赖）
  pipeline_io.py      candidates JSON 读写（原子写入，支持按策略 merge）
  cli.py              preselect 子命令 CLI
  archive_results.py  归档到 data/history/

agent/            LLM 图表复评
  base_reviewer.py    BaseReviewer 基类（流程控制、标准化评分、汇总 suggestion.json）
  gemini_cli_review.py Gemini CLI 封装（批量提交、断点续跑、预算控制）
  prompt.md           Gemini 复评 prompt 模板

dashboard/        Streamlit 看盘界面 + 图表导出
  export_kline_charts.py  导出候选 K 线图到 data/kline/

workbench/        本地工作台（独立 Streamlit 入口）
  app.py           不依赖 dashboard/app.py，集成了全流程运行和 paper_trading 功能

paper_trading/    模拟交易（独立子系统）

config/           YAML 配置文件
data/             运行产物目录（.gitignore 已排除，仅本地存在）
docs/             方案文档
```

## 关键设计

- **数据流向**：`data/raw/*.csv` → candidates → kline charts → review JSONs → suggestion.json → data/history/
- **策略去重**：同一只股票可保留不同策略的候选（key = code + strategy），写文件时默认去重，`--merge-same-date` 按策略维度合并
- **复评标准化**：`BaseReviewer.normalize_scores()` 是评分归一化的唯一入口，四大维度 (trend/price/volume/abnormal) + 经典图形加成，volume_behavior ≤ 1 直接 FAIL
- **断点续跑**：`gemini_cli_review.py` 通过 `skip_existing: true` 跳过已有 review JSON 的股票
- **原子写入**：所有 JSON 落盘均走 `.tmp → os.replace`，防止下游读到半写文件
- **项目根定位**：各模块通过 `Path(__file__).resolve().parent.parent` 定位 `ROOT`，不依赖工作目录
