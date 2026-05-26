"""
base_reviewer.py
~~~~~~~~~~~~~~~~
提供 LLM 图表分析的基础架构：
- 加载配置和 prompt
- 读取候选股票列表
- 查找本地 K 线图
- 遍历调用子类实现的单股评分模型
- 结果汇总和输出
"""

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class BaseReviewer:
    DEFAULT_CLASSIC_PATTERN_STRATEGIES: tuple[str, ...] = ("b1", "b2", "brick")
    BASE_SCORE_WEIGHTS: dict[str, float] = {
        "trend_structure": 0.20,
        "price_position": 0.20,
        "volume_behavior": 0.30,
        "previous_abnormal_move": 0.30,
    }
    CLASSIC_PATTERN_BONUS_WEIGHT: float = 0.10

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.prompt = self.load_prompt(Path(config["prompt_path"]))
        self.kline_dir = Path(config["kline_dir"])
        self.output_dir = Path(config["output_dir"])

    @staticmethod
    def load_prompt(prompt_path: Path) -> str:
        return prompt_path.read_text(encoding="utf-8")

    @staticmethod
    def load_candidates(path: Path) -> dict:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def find_chart_images(self, pick_date: str, code: str) -> Optional[Path]:
        date_dir = self.kline_dir / pick_date
        day_chart = date_dir / f"{code}_day.jpg"
        if not day_chart.exists():
            day_chart_png = date_dir / f"{code}_day.png"
            day_chart = day_chart_png if day_chart_png.exists() else None
        return day_chart

    @staticmethod
    def review_key(code: str, strategy: str = "") -> str:
        suffix = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(strategy or "").strip())
        return f"{code}_{suffix}" if suffix else code

    @classmethod
    def candidate_review_key(cls, candidate: dict) -> str:
        return cls.review_key(str(candidate.get("code") or ""), str(candidate.get("strategy") or ""))

    @classmethod
    def result_review_key(cls, result: dict) -> str:
        if result.get("review_key"):
            return str(result["review_key"])
        return cls.review_key(str(result.get("code") or ""), str(result.get("strategy") or ""))

    @classmethod
    def review_file(cls, out_dir: Path, code: str, strategy: str = "") -> Path:
        return out_dir / f"{cls.review_key(code, strategy)}.json"

    @staticmethod
    def _strategy_list(value: Any) -> list[str]:
        if not value:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @classmethod
    def _classic_pattern_enabled_strategies(cls, value: Any) -> set[str]:
        if isinstance(value, dict):
            if "classic_pattern_enabled" in value:
                if not bool(value.get("classic_pattern_enabled")):
                    return set()
                strategies = list(cls.DEFAULT_CLASSIC_PATTERN_STRATEGIES)
            elif "classic_pattern_strategies" in value:
                strategies = cls._strategy_list(value.get("classic_pattern_strategies"))
            else:
                strategies = list(cls.DEFAULT_CLASSIC_PATTERN_STRATEGIES)
        elif isinstance(value, bool):
            strategies = list(cls.DEFAULT_CLASSIC_PATTERN_STRATEGIES) if value else []
        elif value is None:
            strategies = list(cls.DEFAULT_CLASSIC_PATTERN_STRATEGIES)
        else:
            strategies = cls._strategy_list(value)
        return {strategy.lower() for strategy in strategies}

    @staticmethod
    def is_composite_strategy(strategy: str) -> bool:
        return any(separator in strategy for separator in ("+", "&", ",", "|"))

    @classmethod
    def has_classic_pattern_review(cls, strategy: str, classic_pattern_config: Any = None) -> bool:
        normalized = str(strategy or "").strip().lower()
        if not normalized or cls.is_composite_strategy(normalized):
            return False
        enabled = cls._classic_pattern_enabled_strategies(classic_pattern_config)
        return "*" in enabled or normalized in enabled

    def review_priority_strategies(self, candidates_data: dict) -> list[str]:
        configured = self._strategy_list(
            self.config.get("review_priority_strategies")
            or self.config.get("priority_strategies")
        )
        if configured:
            return configured
        meta = candidates_data.get("meta") or {}
        return self._strategy_list(meta.get("replaced_strategies"))

    def order_candidates_for_review(self, candidates_data: dict) -> list[dict]:
        candidates = list(candidates_data.get("candidates") or [])
        priority = self.review_priority_strategies(candidates_data)
        if not priority:
            return candidates

        priority_index = {strategy: index for index, strategy in enumerate(priority)}
        indexed_candidates = list(enumerate(candidates))
        indexed_candidates.sort(
            key=lambda item: (
                str(item[1].get("strategy") or "") not in priority_index,
                priority_index.get(str(item[1].get("strategy") or ""), len(priority_index)),
                item[0],
            )
        )
        return [candidate for _, candidate in indexed_candidates]

    @staticmethod
    def extract_json(text: str) -> dict:
        code_block = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if code_block:
            text = code_block.group(1)
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError(f"未能在模型输出中找到 JSON 对象:\n{text}")
        return json.loads(text[start:end])

    def review_stock(self, code: str, day_chart: Path, prompt: str, strategy: str = "") -> dict:
        """子类需实现此方法，调用具体的 LLM 进行打分，并返回 JSON 解析字典。"""
        raise NotImplementedError("子类必须实现 review_stock 方法")

    @staticmethod
    def _numeric_score(value: Any) -> float | None:
        try:
            score = float(value)
        except (TypeError, ValueError):
            return None
        if not 0 <= score <= 5:
            return None
        return score

    @classmethod
    def classic_pattern_score(cls, result: dict) -> float | None:
        scores = result.get("scores") or {}
        if not isinstance(scores, dict):
            return None
        return cls._numeric_score(scores.get("classic_pattern_match"))

    @classmethod
    def normalize_scores(cls, result: dict, classic_pattern_config: Any = None) -> dict:
        strategy = str(result.get("strategy") or "").strip().lower()
        scores = result.get("scores") or {}
        if not isinstance(scores, dict):
            return result

        normalized_scores: dict[str, float] = {}
        for key in cls.BASE_SCORE_WEIGHTS:
            score = cls._numeric_score(scores.get(key))
            if score is None:
                return result
            normalized_scores[key] = score

        merged_scores = {**scores, **normalized_scores}
        has_classic_pattern = cls.has_classic_pattern_review(strategy, classic_pattern_config)
        base_score = sum(
            normalized_scores[key] * weight
            for key, weight in cls.BASE_SCORE_WEIGHTS.items()
        )
        classic_bonus = 0.0

        if not has_classic_pattern:
            merged_scores["classic_pattern_match"] = 0.0
            result["classic_pattern_type"] = "none"
            result["classic_pattern_reasoning"] = ""
        else:
            classic_score = cls._numeric_score(scores.get("classic_pattern_match"))
            if classic_score is None:
                return result
            classic_score = max(1.0, classic_score)
            normalized_scores["classic_pattern_match"] = classic_score
            merged_scores["classic_pattern_match"] = classic_score
            classic_bonus = max(0.0, classic_score - 1.0) * cls.CLASSIC_PATTERN_BONUS_WEIGHT

        result["scores"] = merged_scores
        result["total_score"] = round(min(5.0, base_score + classic_bonus), 2)

        if normalized_scores["volume_behavior"] <= 1:
            result["verdict"] = "FAIL"
        elif result["total_score"] >= 4.0:
            result["verdict"] = "PASS"
        elif result["total_score"] >= 3.2:
            result["verdict"] = "WATCH"
        else:
            result["verdict"] = "FAIL"

        return result

    def generate_suggestion(
        self,
        pick_date: str,
        all_results: List[dict],
        min_score: float,
        candidates: List[dict] | None = None,
    ) -> dict:
        candidates = candidates or []
        key_to_strategy = {
            self.candidate_review_key(candidate): str(candidate.get("strategy") or "")
            for candidate in candidates
            if candidate.get("code")
        }
        result_by_key = {self.result_review_key(result): result for result in all_results if result.get("code")}
        passed = [r for r in all_results if r.get("total_score", 0) >= min_score]
        excluded = [self.result_review_key(r) for r in all_results if r.get("total_score", 0) < min_score]

        strategy_counts: dict[str, dict[str, int]] = {}
        for candidate in candidates:
            code = str(candidate.get("code") or "")
            if not code:
                continue
            review_key = self.candidate_review_key(candidate)
            strategy = str(candidate.get("strategy") or "unknown")
            counts = strategy_counts.setdefault(
                strategy,
                {"total": 0, "reviewed": 0, "recommended": 0, "excluded": 0, "pending": 0},
            )
            counts["total"] += 1
            result = result_by_key.get(review_key)
            if not result:
                counts["pending"] += 1
                continue
            counts["reviewed"] += 1
            if result.get("total_score", 0) >= min_score:
                counts["recommended"] += 1
            else:
                counts["excluded"] += 1

        passed.sort(key=lambda r: r.get("total_score", 0), reverse=True)

        recommendations = [
            {
                "rank": i + 1,
                "code": r["code"],
                "strategy": r.get("strategy") or key_to_strategy.get(self.result_review_key(r), ""),
                "review_key": self.result_review_key(r),
                "verdict": r.get("verdict", ""),
                "total_score": r.get("total_score", 0),
                "signal_type": r.get("signal_type", ""),
                "classic_pattern_type": r.get("classic_pattern_type", ""),
                "classic_pattern_match": self.classic_pattern_score(r),
                "classic_pattern_reasoning": r.get("classic_pattern_reasoning", ""),
                "comment": r.get("comment", ""),
            }
            for i, r in enumerate(passed)
        ]

        return {
            "date": pick_date,
            "min_score_threshold": min_score,
            "total_reviewed": len(all_results),
            "recommendations": recommendations,
            "excluded": excluded,
            "strategy_counts": strategy_counts,
        }

    def run(self):
        candidates_data = self.load_candidates(Path(self.config["candidates"]))
        pick_date: str = candidates_data["pick_date"]
        candidates: List[dict] = self.order_candidates_for_review(candidates_data)
        print(f"[INFO] pick_date={pick_date}，候选股票数={len(candidates)}")
        priority = self.review_priority_strategies(candidates_data)
        if priority:
            print(f"[INFO] 复评优先策略: {', '.join(priority)}")

        out_dir = self.output_dir / pick_date
        out_dir.mkdir(parents=True, exist_ok=True)

        all_results: List[dict] = []
        failed_codes: List[str] = []

        for i, candidate in enumerate(candidates, 1):
            code: str = candidate["code"]
            strategy = str(candidate.get("strategy") or "")
            review_key = self.review_key(code, strategy)
            out_file = self.review_file(out_dir, code, strategy)

            if self.config.get("skip_existing", False) and out_file.exists():
                print(f"[{i}/{len(candidates)}] {review_key} — 已存在，跳过。")
                with open(out_file, encoding="utf-8") as f:
                    result = json.load(f)
                result = self.normalize_scores(result, self.config)
                all_results.append(result)
                continue

            day_chart = self.find_chart_images(pick_date, code)
            if day_chart is None:
                print(f"[{i}/{len(candidates)}] {review_key} — 缺少日线图，跳过。")
                failed_codes.append(review_key)
                continue

            print(f"[{i}/{len(candidates)}] {review_key} — 正在分析 ...", end=" ", flush=True)

            try:
                result = self.review_stock(
                    code=code,
                    day_chart=day_chart,
                    prompt=self.prompt,
                    strategy=strategy,
                )
                result["strategy"] = strategy or result.get("strategy", "")
                result["review_key"] = review_key
                result = self.normalize_scores(result, self.config)
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                all_results.append(result)
                verdict = result.get("verdict", "?")
                score = result.get("total_score", "?")
                print(f"完成 — verdict={verdict}, score={score}")
            except Exception as e:
                print(f"失败 — {e}")
                failed_codes.append(review_key)

            if i < len(candidates):
                time.sleep(self.config.get("request_delay", 5))

        print(f"\n[INFO] 评分完成：成功 {len(all_results)} 支，失败/跳过 {len(failed_codes)} 支")
        if failed_codes:
            print(f"[WARN] 未处理股票：{failed_codes}")

        if not all_results:
            print("[ERROR] 没有可用的评分结果，跳过汇总。")
            return

        print("\n[INFO] 正在生成汇总推荐建议 ...")
        min_score = self.config.get("suggest_min_score", 4.0)
        suggestion = self.generate_suggestion(
            pick_date=pick_date,
            all_results=all_results,
            min_score=min_score,
            candidates=candidates,
        )
        suggestion_file = out_dir / "suggestion.json"
        with open(suggestion_file, "w", encoding="utf-8") as f:
            json.dump(suggestion, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 汇总推荐已写入: {suggestion_file}")
        print(f"       推荐股票数（score≥{min_score}）: {len(suggestion['recommendations'])}")
        for strategy, counts in sorted(suggestion.get("strategy_counts", {}).items()):
            print(f"       {strategy}: 推荐 {counts.get('recommended', 0)} / 候选 {counts.get('total', 0)}")

        print("\n✅ 全部完成。")
        print(f"   输出目录: {out_dir}")
