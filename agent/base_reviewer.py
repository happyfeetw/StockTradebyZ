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

from review_scoring import (
    BASE_SCORE_WEIGHTS,
    CLASSIC_PATTERN_BONUS_WEIGHT,
    build_prompt_context,
    normalize_common_gate,
    numeric_score,
    scoring_config,
    strategy_profile,
    strategy_thresholds,
    weighted_score,
)


class BaseReviewer:
    DEFAULT_CLASSIC_PATTERN_STRATEGIES: tuple[str, ...] = ("b1", "b2", "brick")
    BASE_SCORE_WEIGHTS: dict[str, float] = dict(BASE_SCORE_WEIGHTS)
    CLASSIC_PATTERN_BONUS_WEIGHT: float = CLASSIC_PATTERN_BONUS_WEIGHT

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
        group_by_strategy = bool(self.config.get("group_review_by_strategy", True))
        if not priority and not group_by_strategy:
            return candidates

        priority_index = {strategy: index for index, strategy in enumerate(priority)}
        indexed_candidates = list(enumerate(candidates))
        indexed_candidates.sort(
            key=lambda item: (
                str(item[1].get("strategy") or "") not in priority_index,
                priority_index.get(str(item[1].get("strategy") or ""), len(priority_index)),
                str(item[1].get("strategy") or "") if group_by_strategy else "",
                item[0],
            )
        )
        return [candidate for _, candidate in indexed_candidates]

    @staticmethod
    def batch_strategy(items: list[dict[str, Any]]) -> str:
        strategies = {str(item.get("strategy") or "") for item in items}
        return strategies.pop() if len(strategies) == 1 else ""

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
        return numeric_score(value)

    @classmethod
    def classic_pattern_score(cls, result: dict) -> float | None:
        scores = result.get("scores") or {}
        if not isinstance(scores, dict):
            return None
        return cls._numeric_score(scores.get("classic_pattern_match"))

    @classmethod
    def strategy_thresholds(cls, strategy: str, config: Any = None, fallback: float = 4.0) -> tuple[float, float]:
        return strategy_thresholds(strategy, config, fallback=fallback)

    @classmethod
    def is_result_recommended(cls, result: dict, min_score: float = 4.0, config: Any = None) -> bool:
        score = cls._numeric_score(result.get("total_score"))
        if score is None:
            return False
        strategy = str(result.get("strategy") or "")
        pass_min, _ = cls.strategy_thresholds(strategy, config, fallback=min_score)
        return score >= pass_min and str(result.get("verdict") or "").upper() == "PASS"

    def prompt_for_strategy(self, prompt: str, strategy: str = "") -> str:
        return prompt + build_prompt_context(strategy, self.config)

    @classmethod
    def _normalize_legacy_scores(cls, result: dict, classic_pattern_config: Any = None) -> dict:
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
        total_score = round(min(5.0, base_score + classic_bonus), 2)

        if normalized_scores["volume_behavior"] <= 1:
            if total_score >= 4.0:
                result["score_before_hard_veto"] = total_score
                result["hard_veto_reason"] = "volume_behavior <= 1"
                total_score = 3.99
            result["total_score"] = total_score
            result["verdict"] = "FAIL"
        elif total_score >= 4.0:
            result["total_score"] = total_score
            result["verdict"] = "PASS"
        elif total_score >= 3.2:
            result["total_score"] = total_score
            result["verdict"] = "WATCH"
        else:
            result["total_score"] = total_score
            result["verdict"] = "FAIL"

        return result

    @classmethod
    def normalize_scores(cls, result: dict, classic_pattern_config: Any = None) -> dict:
        strategy = str(result.get("strategy") or "").strip().lower()
        profile = strategy_profile(strategy, classic_pattern_config)
        if not profile:
            return cls._normalize_legacy_scores(result, classic_pattern_config)

        scores = result.get("scores") or {}
        if not isinstance(scores, dict):
            return result

        has_classic_pattern = cls.has_classic_pattern_review(strategy, classic_pattern_config)
        normalized_scores: dict[str, float] = {}
        required_fields = set(profile.get("weights") or {})
        required_fields.update(cls.BASE_SCORE_WEIGHTS)
        for key in required_fields:
            score = cls._numeric_score(scores.get(key))
            if score is None:
                return result
            if key == "classic_pattern_match" and has_classic_pattern:
                score = max(1.0, score)
            normalized_scores[key] = score

        merged_scores = {**scores, **normalized_scores}
        if not has_classic_pattern:
            merged_scores["classic_pattern_match"] = 0.0
            result["classic_pattern_type"] = "none"
            result["classic_pattern_reasoning"] = ""

        common_gate = normalize_common_gate({**result, "scores": merged_scores}, classic_pattern_config)
        result["common_gate"] = common_gate
        result["common_gate_score"] = common_gate["score"]
        result["common_gate_status"] = common_gate["status"]

        disabled_fields = set()
        if not has_classic_pattern:
            disabled_fields.add("classic_pattern_match")
        strategy_score = weighted_score(merged_scores, profile.get("weights") or {}, disabled_fields=disabled_fields)
        if strategy_score is None:
            return result
        strategy_score = round(float(strategy_score), 2)
        result["scores"] = merged_scores
        result["strategy_score"] = strategy_score
        result["score_profile"] = {
            "strategy": strategy,
            "label": profile.get("label", strategy),
            "pass_min": float(profile.get("pass_min", 4.0)),
            "watch_min": float(profile.get("watch_min", 3.2)),
            "weights": profile.get("weights") or {},
        }

        hard_veto_reasons = list(common_gate.get("hard_veto_reasons") or [])
        for field, max_score in (profile.get("hard_veto_score_max") or {}).items():
            if str(field) in disabled_fields:
                continue
            score = cls._numeric_score(merged_scores.get(str(field)))
            if score is not None and score <= float(max_score):
                hard_veto_reasons.append(f"strategy_profile.{field} <= {float(max_score):g}")

        strategy_watch_cap_reasons: list[str] = []
        for field, max_score in (profile.get("watch_cap_score_max") or {}).items():
            if str(field) in disabled_fields:
                continue
            score = cls._numeric_score(merged_scores.get(str(field)))
            if score is not None and score <= float(max_score):
                strategy_watch_cap_reasons.append(f"strategy_profile.{field} <= {float(max_score):g}")

        pass_min = float(profile.get("pass_min", 4.0))
        watch_min = float(profile.get("watch_min", 3.2))
        total_score = strategy_score
        verdict = "FAIL"
        if hard_veto_reasons:
            result["score_before_hard_veto"] = total_score
            total_score = min(total_score, pass_min - 0.01)
            verdict = "FAIL"
        elif common_gate["status"] == "FAIL":
            total_score = min(total_score, pass_min - 0.01)
            verdict = "FAIL"
        elif common_gate["status"] == "WATCH" and scoring_config(classic_pattern_config)["common_gate"].get("watch_caps_strategy_pass", True):
            result["score_before_common_gate_cap"] = total_score
            total_score = min(total_score, pass_min - 0.01)
            verdict = "WATCH" if total_score >= watch_min else "FAIL"
        elif strategy_watch_cap_reasons and total_score >= pass_min:
            result["score_before_strategy_cap"] = total_score
            total_score = min(total_score, pass_min - 0.01)
            verdict = "WATCH" if total_score >= watch_min else "FAIL"
        elif total_score >= pass_min:
            verdict = "PASS"
        elif total_score >= watch_min:
            verdict = "WATCH"

        result["total_score"] = round(max(0.0, min(5.0, total_score)), 2)
        result["verdict"] = verdict
        if hard_veto_reasons:
            result["hard_veto_reason"] = "; ".join(hard_veto_reasons)
            result["hard_veto_reasons"] = hard_veto_reasons
        elif strategy_watch_cap_reasons:
            result["watch_cap_reason"] = "; ".join(strategy_watch_cap_reasons)
            result["watch_cap_reasons"] = strategy_watch_cap_reasons
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
        config = getattr(self, "config", {})
        passed = [r for r in all_results if self.is_result_recommended(r, min_score, config)]
        excluded = [self.result_review_key(r) for r in all_results if not self.is_result_recommended(r, min_score, config)]

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
            if self.is_result_recommended(result, min_score, config):
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
                "strategy_score": r.get("strategy_score", ""),
                "common_gate_score": r.get("common_gate_score", ""),
                "common_gate_status": r.get("common_gate_status", ""),
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
            "score_threshold_mode": "strategy_profile",
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
