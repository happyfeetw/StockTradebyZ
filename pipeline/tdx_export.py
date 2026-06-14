"""
pipeline/tdx_export.py
通达信 .blk 板块文件生成。

职责：
  - 读取 candidates + review 数据
  - 按策略分组、排序
  - 股票代码转通达信格式
  - 生成 GBK 编码的 .blk 内容（用于浏览器 File System Access API 写入）
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def review_key(code: str, strategy: str = "") -> str:
    suffix = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(strategy or "").strip())
    return f"{code}_{suffix}" if suffix else code


def _encode_fixed_gbk(text: str, width: int) -> bytes:
    raw = str(text or "").encode("gbk", errors="ignore")[:width]
    return raw + b"\x00" * (width - len(raw))


def _decode_fixed_gbk(raw: bytes) -> str:
    return raw.split(b"\x00")[0].decode("gbk", errors="ignore").strip()


def cfg_record_bytes(name: str) -> bytes:
    """Build one blocknew.cfg record: 50-byte display name + 70-byte file stem."""
    return _encode_fixed_gbk(name, 50) + _encode_fixed_gbk(name, 70)


def _cfg_record_b64(name: str) -> str:
    return base64.b64encode(cfg_record_bytes(name)).decode("ascii")


def date_suffix(pick_date: str) -> str:
    """Return YYYYMMDD when possible, otherwise a digit-only fallback."""
    text = str(pick_date or "").strip()
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return "".join(match.groups())
    digits = re.sub(r"\D+", "", text)
    return digits or "unknown"


def import_bat_filename(pick_date: str) -> str:
    return f"import_to_tdx_{date_suffix(pick_date)}.bat"


def import_html_filename(pick_date: str, mode_label: str) -> str:
    if mode_label == "仅推荐":
        mode_suffix = "recommended"
    elif mode_label == "全部候选":
        mode_suffix = "all"
    else:
        text = str(mode_label or "")
        if "共同推荐" in text:
            mode_suffix = "consensus_all_recommended"
        elif "多模型推荐" in text:
            mode_suffix = "consensus_multi_recommended"
        elif "单模型推荐" in text:
            mode_suffix = "consensus_single_recommended"
        elif "共同观察" in text:
            mode_suffix = "consensus_all_watch"
        elif "多模型观察" in text:
            mode_suffix = "consensus_multi_watch"
        elif "单模型观察" in text:
            mode_suffix = "consensus_single_watch"
        elif "Z精选+观察" in text:
            mode_suffix = "z_select_watch"
        elif "Z精选" in text:
            mode_suffix = "z_select"
        elif "Z观察" in text:
            mode_suffix = "z_watch"
        elif "Z复盘" in text:
            mode_suffix = "z_review_only"
        elif "观察" in text:
            mode_suffix = "consensus_watch"
        elif "分歧" in text:
            mode_suffix = "consensus_divergent"
        elif "共识" in text:
            mode_suffix = "consensus"
        else:
            mode_suffix = re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_").lower() or "custom"
    return f"tdx_import_{date_suffix(pick_date)}_{mode_suffix}.html"


def merge_cfg_records(cfg_bytes: bytes, blocks: list[dict[str, Any]]) -> tuple[bytes, bool, int]:
    """Append missing block records while preserving existing 120-byte records."""
    record_size = 120
    if len(cfg_bytes) % record_size != 0:
        raise ValueError(f"blocknew.cfg 长度不是 120 字节记录的整数倍: {len(cfg_bytes)}")

    existing_records: list[bytes] = []
    existing_abbrs: set[str] = set()
    for offset in range(0, len(cfg_bytes), record_size):
        record = cfg_bytes[offset : offset + record_size]
        existing_records.append(record)
        existing_abbrs.add(_decode_fixed_gbk(record[50:120]).lower())

    added = 0
    for block in blocks:
        name = str(block.get("name") or "").strip()
        if not name:
            continue
        if name.lower() in existing_abbrs:
            continue
        existing_records.append(cfg_record_bytes(name))
        existing_abbrs.add(name.lower())
        added += 1

    if added == 0:
        return cfg_bytes, False, 0
    return b"".join(existing_records), True, added


def _backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.agentrader-{stamp}.bak")
    shutil.copy2(path, backup)
    return backup


def to_tdx_code(code: str) -> str | None:
    """6位代码 → 通达信板块格式（市场标识 + 代码）。无法识别返回 None。

    通达信 blk 文件市场标识：0=深圳 1=上海 2=北交所
    """
    code = code.strip().zfill(6)
    if not re.fullmatch(r"\d{6}", code):
        return None
    if code.startswith(("60", "68")):
        return f"1{code}"  # 上海市场
    if code.startswith(("00", "30")):
        return f"0{code}"  # 深圳市场
    if code.startswith(("43", "83", "87", "88", "92")):
        return f"2{code}"  # 北交所/新三板常见代码段
    return None


def _date_mmdd(pick_date: str) -> str:
    try:
        dt = pick_date.strip()
        if len(dt) == 10 and dt[4] == "-":
            return dt[5:7] + dt[8:10]
        return dt[4:6] + dt[6:8] if len(dt) >= 8 else dt
    except (IndexError, ValueError):
        return pick_date.replace("-", "")[-4:]


def _block_name(pick_date: str, strategy: str, prefix: str = "Q") -> str:
    """生成板块文件名：MMDD + 前缀 + 策略名，如 0529QB1。"""
    mmdd = _date_mmdd(pick_date)
    safe_prefix = re.sub(r"[^0-9A-Za-z]+", "", str(prefix or "Q"))[:4] or "Q"
    strategy_display = {"b1": "B1", "b2": "B2", "brick": "Brick"}.get(
        strategy.lower(), strategy
    )
    return f"{mmdd}{safe_prefix}{strategy_display}"


def _strategy_label(strategy: str) -> str:
    """将内部策略名转为板块命名中的显示名。"""
    return {"b1": "B1", "b2": "B2", "brick": "Brick"}.get(strategy.lower(), strategy)


def _suggestion_recommendations(pick_date: str, min_score: float | None) -> tuple[dict[str, dict], float]:
    suggestion_path = _PROJECT_ROOT / "data" / "review" / pick_date / "suggestion.json"
    suggestion = _load_json(suggestion_path)
    recommendations: dict[str, dict] = {}
    if suggestion:
        for item in suggestion.get("recommendations", []):
            code = str(item.get("code") or "")
            strategy = str(item.get("strategy") or "")
            key = str(item.get("review_key") or review_key(code, strategy))
            recommendations[key] = item
        if min_score is None:
            min_score = float(suggestion.get("min_score_threshold", 4.0))
    return recommendations, 4.0 if min_score is None else float(min_score)


def _candidate_data_for_date(pick_date: str | None) -> dict[str, Any]:
    candidates_dir = _PROJECT_ROOT / "data" / "candidates"
    if pick_date:
        dated_path = candidates_dir / f"candidates_{pick_date}.json"
        dated = _load_json(dated_path)
        if dated:
            return dated
    latest_path = candidates_dir / "candidates_latest.json"
    latest = _load_json(latest_path)
    if not latest:
        raise FileNotFoundError(f"候选数据不存在: {latest_path}")
    return latest


def _items_from_history(pick_date: str) -> list[dict[str, Any]]:
    history_path = _PROJECT_ROOT / "data" / "history" / pick_date / "all.json"
    history = _load_json(history_path)
    items: list[dict[str, Any]] = []
    for row in history.get("results", []) if history else []:
        code = str(row.get("code") or "")
        strategy = str(row.get("strategy") or "")
        if not code or not strategy:
            continue
        review = row.get("review") or {}
        score = review.get("total_score")
        items.append({
            "code": code,
            "strategy": strategy,
            "score": float(score) if score is not None else None,
            "verdict": review.get("verdict") or "",
            "recommended": row.get("status") == "recommended",
            "rank": row.get("rank"),
        })
    return items


def _items_from_latest_candidates(pick_date: str, recommendations: dict[str, dict]) -> list[dict[str, Any]]:
    candidates_data = _candidate_data_for_date(pick_date)
    candidates: list[dict] = candidates_data.get("candidates", [])
    if not candidates:
        return []

    review_dir = _PROJECT_ROOT / "data" / "review" / pick_date
    review_cache: dict[str, dict] = {}
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        code = str(candidate.get("code") or "")
        strategy = str(candidate.get("strategy") or "")
        if not code or not strategy:
            continue
        key = review_key(code, strategy)
        review = review_cache.get(key)
        if review is None:
            review_file = review_dir / f"{key}.json"
            review = _load_json(review_file) if review_file.exists() else {}
            review_cache[key] = review
        score = review.get("total_score")
        items.append({
            "code": code,
            "strategy": strategy,
            "score": float(score) if score is not None else None,
            "verdict": review.get("verdict") or "",
            "recommended": key in recommendations,
            "rank": recommendations.get(key, {}).get("rank"),
        })
    return items


def _build_block_payload(
    pick_date: str,
    strategy: str,
    items: list[dict[str, Any]],
    *,
    name_prefix: str = "Q",
) -> dict[str, Any] | None:
    lines: list[str] = []
    samples: list[str] = []
    skipped = 0
    for item in items:
        code = str(item.get("code") or "")
        tdx_code = to_tdx_code(code)
        if tdx_code is None:
            skipped += 1
            logger.warning("无法转换代码 %s，已跳过", code)
            continue
        lines.append(tdx_code)
        if len(samples) < 5:
            samples.append(code.zfill(6))
    if not lines:
        return None
    name = _block_name(pick_date, strategy, prefix=name_prefix)
    content_bytes = ("\r\n".join(lines) + "\r\n").encode("gbk")
    return {
        "name": name,
        "content_b64": base64.b64encode(content_bytes).decode("ascii"),
        "cfg_record_b64": _cfg_record_b64(name),
        "count": len(lines),
        "skipped": skipped,
        "samples": samples,
    }


def build_blocks(
    pick_date: str | None = None,
    mode: str = "recommended",
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """
    生成 .blk 板块数据列表。

    参数
    ----
    pick_date : 选股日期，None 则取最新 candidates
    mode : "recommended" 仅推荐 / "all" 全部候选
    min_score : 推荐门槛，None 则读 suggestion.json 中的值

    返回
    ----
    [{"name": "0529QB1", "content_b64": "base64...", "count": 37, "skipped": 0}, ...]
    content_b64 是 GBK 编码后 base64 的结果，供 JS 端直接写入。
    """
    if pick_date is None:
        candidates_data = _candidate_data_for_date(None)
        pick_date = str(candidates_data.get("pick_date", "") or "")
    if not pick_date:
        raise ValueError("无法确定选股日期")

    recommendations, min_score = _suggestion_recommendations(pick_date, min_score)
    source_items = _items_from_history(pick_date)
    if not source_items:
        source_items = _items_from_latest_candidates(pick_date, recommendations)

    # ── 按策略分组 ──────────────────────────────────────────────────
    strategy_groups: dict[str, list[dict]] = {}
    for item in source_items:
        code = str(item.get("code") or "")
        strategy_raw = str(item.get("strategy") or "")
        if not code or not strategy_raw:
            continue
        strategy = _strategy_label(strategy_raw)

        strategy_groups.setdefault(strategy, []).append({
            "code": code,
            "score": item.get("score"),
            "verdict": item.get("verdict") or "",
            "recommended": bool(item.get("recommended")),
            "rank": item.get("rank"),
        })

    # ── 排序 ────────────────────────────────────────────────────────
    for strategy, items in strategy_groups.items():
        if mode == "recommended":
            items[:] = [
                item for item in items
                if item["recommended"] and (item["score"] or 0) >= min_score
            ]
            items.sort(key=lambda x: x["score"] or 0, reverse=True)
        else:
            # 推荐（score 降序）→ 已复评（score 降序）→ 未复评（代码升序）
            recommended = [x for x in items if x["recommended"]]
            reviewed = [
                x for x in items
                if not x["recommended"] and x["score"] is not None
            ]
            unreviewed = [
                x for x in items
                if not x["recommended"] and x["score"] is None
            ]
            recommended.sort(key=lambda x: x["score"] or 0, reverse=True)
            reviewed.sort(key=lambda x: x["score"] or 0, reverse=True)
            unreviewed.sort(key=lambda x: x["code"])
            items[:] = recommended + reviewed + unreviewed

    # ── 生成 .blk 内容 ──────────────────────────────────────────────
    blocks: list[dict[str, Any]] = []
    for strategy in sorted(strategy_groups.keys()):
        items = strategy_groups[strategy]
        if not items:
            continue
        block = _build_block_payload(pick_date, strategy, items)
        if block:
            blocks.append(block)

    return blocks


def build_blocks_from_items(
    pick_date: str,
    source_items: list[dict[str, Any]],
    *,
    name_prefix: str = "C",
) -> list[dict[str, Any]]:
    """从已筛选条目生成按策略分组的通达信板块。

    `source_items` 至少需要包含 code、strategy，可选 score/recommended/rank。
    该 helper 供共识结果等非正式 Gemini 来源复用导入链路。
    """
    if not pick_date:
        raise ValueError("无法确定选股日期")

    strategy_groups: dict[str, list[dict[str, Any]]] = {}
    for item in source_items:
        code = str(item.get("code") or "")
        strategy_raw = str(item.get("strategy") or "")
        if not code or not strategy_raw:
            continue
        strategy = _strategy_label(strategy_raw)
        strategy_groups.setdefault(strategy, []).append(
            {
                "code": code,
                "score": item.get("score"),
                "recommended": bool(item.get("recommended")),
                "rank": item.get("rank"),
            }
        )

    for items in strategy_groups.values():
        items.sort(
            key=lambda x: (
                not bool(x.get("recommended")),
                -(float(x.get("score") or 0)),
                str(x.get("code") or ""),
            )
        )

    blocks: list[dict[str, Any]] = []
    for strategy in sorted(strategy_groups.keys()):
        block = _build_block_payload(
            pick_date,
            strategy,
            strategy_groups[strategy],
            name_prefix=name_prefix,
        )
        if block:
            blocks.append(block)
    return blocks


def export_to_tdx(
    blocknew_dir: str | Path, blocks: list[dict[str, Any]]
) -> dict[str, Any]:
    """
    将板块内容（b64 编码的 gbk 字符串）写入指定目录的 .blk 文件，并自动在 blocknew.cfg 中注册。

    参数
    ----
    blocknew_dir : 通达信 T0002/blocknew 目录的绝对路径
    blocks : build_blocks() 返回的板块列表

    返回
    ----
    {"succeeded": int, "failed": int, "cfg_ok": bool, "error": str}
    """
    dir_path = Path(blocknew_dir)
    if not dir_path.exists() or not dir_path.is_dir():
        return {
            "succeeded": 0,
            "failed": len(blocks),
            "cfg_ok": False,
            "error": "指定的通达信目录不存在或不是目录",
        }

    succeeded = 0
    failed = 0
    written_blocks: list[dict[str, Any]] = []

    # 1. 写入所有 .blk 文件
    for b in blocks:
        name = b["name"]
        content_b64 = b["content_b64"]
        try:
            content_bytes = base64.b64decode(content_b64)
            blk_path = dir_path / f"{name}.blk"
            with open(blk_path, "wb") as f:
                f.write(content_bytes)
            succeeded += 1
            written_blocks.append(b)
        except Exception as e:
            failed += 1
            logger.error("写入板块文件 %s.blk 失败: %s", name, e)

    # 2. 更新 blocknew.cfg
    cfg_ok = True
    cfg_error = ""
    cfg_path = dir_path / "blocknew.cfg"

    try:
        if failed:
            raise RuntimeError("部分 .blk 文件写入失败，已跳过 blocknew.cfg 注册以避免索引指向缺失文件")
        cfg_bytes = b""
        if cfg_path.exists():
            with open(cfg_path, "rb") as f:
                cfg_bytes = f.read()

        new_cfg_bytes, modified, _added = merge_cfg_records(cfg_bytes, written_blocks)
        if modified:
            _backup_file(cfg_path)
            with open(cfg_path, "wb") as f:
                f.write(new_cfg_bytes)

    except Exception as e:
        cfg_ok = False
        cfg_error = str(e)
        logger.error("更新 blocknew.cfg 失败: %s", e)

    return {
        "succeeded": succeeded,
        "failed": failed,
        "cfg_ok": cfg_ok,
        "error": cfg_error,
    }


def generate_import_bat(blocks: list[dict[str, Any]]) -> str:
    """
    生成适用于 Windows 的一键导入批处理脚本。

    BAT itself stays ASCII and writes a base64-encoded PowerShell payload to a
    temp file. The final pause is handled by CMD so syntax/runtime errors do not
    make the double-clicked window disappear immediately.
    """
    def ps_quote(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    ps_blocks = []
    for b in blocks:
        ps_blocks.append(
            f"    @{{ Name = {ps_quote(str(b['name']))}; Content = {ps_quote(str(b['content_b64']))} }}"
        )

    ps_blocks_str = ",\n".join(ps_blocks)

    ps_script = f"""
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Get-TdxEncoding {{
    try {{
        return [System.Text.Encoding]::GetEncoding(936)
    }} catch {{
        return [System.Text.Encoding]::Default
    }}
}}

function New-CfgRecord([string]$Name, $Encoding) {{
    $record = New-Object byte[] 120
    $nameBytes = $Encoding.GetBytes($Name)
    $abbrBytes = $Encoding.GetBytes($Name)
    [System.Array]::Copy($nameBytes, 0, $record, 0, [Math]::Min($nameBytes.Length, 50))
    [System.Array]::Copy($abbrBytes, 0, $record, 50, [Math]::Min($abbrBytes.Length, 70))
    return $record
}}

function Get-CfgAbbr([byte[]]$Record, $Encoding) {{
    $abbrRaw = New-Object byte[] 70
    [System.Array]::Copy($Record, 50, $abbrRaw, 0, 70)
    $nullIdx = [System.Array]::IndexOf($abbrRaw, [byte]0)
    if ($nullIdx -lt 0) {{ $nullIdx = 70 }}
    return $Encoding.GetString($abbrRaw, 0, $nullIdx).Trim()
}}

function Normalize-PathText([string]$PathText) {{
    if (-not $PathText) {{ return "" }}
    return $PathText.Trim().Trim('"').Trim("'")
}}

function Is-BlocknewPath([string]$PathText) {{
    $path = Normalize-PathText $PathText
    if (-not $path) {{ return $false }}
    $leaf = Split-Path -Leaf $path
    $parent = Split-Path -Parent $path
    $parentLeaf = if ($parent) {{ Split-Path -Leaf $parent }} else {{ "" }}
    return ($leaf.ToLower() -eq "blocknew" -and $parentLeaf.ToLower() -eq "t0002")
}}

function Save-BlocknewDir([string]$PathText) {{
    $configPath = Join-Path $HOME ".tdx_import_path"
    $PathText | Out-File $configPath -Force -Encoding utf8
}}

function Add-BlocknewCandidate($Candidates, [string]$PathText) {{
    $path = Normalize-PathText $PathText
    if (-not $path) {{ return }}
    if (-not (Is-BlocknewPath $path)) {{ return }}
    if (-not (Test-Path -LiteralPath $path -PathType Container)) {{ return }}
    foreach ($existing in $Candidates) {{
        if ($existing.ToLower() -eq $path.ToLower()) {{ return }}
    }}
    [void]$Candidates.Add($path)
}}

function Add-NearbyBlocknewCandidates($Candidates, [string]$BaseDir) {{
    $dir = Normalize-PathText $BaseDir
    while ($dir) {{
        Add-BlocknewCandidate $Candidates $dir
        Add-BlocknewCandidate $Candidates (Join-Path $dir "T0002\\blocknew")
        $parent = Split-Path -Parent $dir
        if (-not $parent -or $parent -eq $dir) {{ break }}
        $dir = $parent
    }}
}}

function Search-BlocknewCandidates($Candidates, [string]$Root, [int]$MaxDepth, [int]$MaxDirs) {{
    $rootPath = Normalize-PathText $Root
    if (-not $rootPath -or -not (Test-Path -LiteralPath $rootPath -PathType Container)) {{ return }}

    $queue = [System.Collections.Queue]::new()
    $queue.Enqueue([PSCustomObject]@{{ Path = $rootPath; Depth = 0 }})
    $visited = 0

    while ($queue.Count -gt 0 -and $visited -lt $MaxDirs) {{
        $item = $queue.Dequeue()
        $visited++
        try {{
            $children = Get-ChildItem -LiteralPath $item.Path -Directory -ErrorAction SilentlyContinue
        }} catch {{
            continue
        }}
        foreach ($child in $children) {{
            $direct = Join-Path $child.FullName "T0002\\blocknew"
            Add-BlocknewCandidate $Candidates $direct

            if ($child.Name.ToLower() -eq "blocknew") {{
                Add-BlocknewCandidate $Candidates $child.FullName
            }}

            if ($item.Depth -lt $MaxDepth) {{
                [void]$queue.Enqueue([PSCustomObject]@{{ Path = $child.FullName; Depth = ($item.Depth + 1) }})
            }}
        }}
    }}
}}

function Resolve-BlocknewDir {{
    $configPath = Join-Path $HOME ".tdx_import_path"
    if (Test-Path $configPath) {{
        $saved = Normalize-PathText (Get-Content $configPath -Raw)
        if ($saved -and (Is-BlocknewPath $saved) -and (Test-Path -LiteralPath $saved -PathType Container)) {{
            return $saved
        }}
    }}

    $candidates = [System.Collections.ArrayList]::new()

    $currentDir = ""
    if ($env:AGENTTRADER_BAT_DIR) {{
        $currentDir = $env:AGENTTRADER_BAT_DIR.TrimEnd("\\")
    }}
    if ($currentDir) {{
        Add-NearbyBlocknewCandidates $candidates $currentDir
    }}

    $driveRoots = @(Get-PSDrive -PSProvider FileSystem | Where-Object {{ $_.Root }} | ForEach-Object {{ $_.Root.TrimEnd("\\") }})
    $installNames = @(
        "new_tdx", "tdx", "tdxw", "TdxW", "通达信",
        "zd_ths", "zd_gftdx", "gftdx", "vipdoc",
        "htzq", "gjzq", "gdzq", "gfzq", "hxzq", "zszq", "zxzq", "zjzq"
    )
    foreach ($root in $driveRoots) {{
        foreach ($name in $installNames) {{
            Add-BlocknewCandidate $candidates (Join-Path $root "$name\\T0002\\blocknew")
        }}
    }}

    $scanRoots = [System.Collections.ArrayList]::new()
    foreach ($root in $driveRoots) {{ [void]$scanRoots.Add($root + "\\") }}
    foreach ($path in @($env:ProgramFiles, ${{env:ProgramFiles(x86)}}, $HOME, (Join-Path $HOME "Desktop"), (Join-Path $HOME "Downloads"))) {{
        if ($path -and (Test-Path -LiteralPath $path -PathType Container)) {{ [void]$scanRoots.Add($path) }}
    }}
    foreach ($root in $scanRoots) {{
        Search-BlocknewCandidates $candidates $root 2 600
    }}

    if ($candidates.Count -gt 0) {{
        $withCfg = @()
        foreach ($candidate in $candidates) {{
            if (Test-Path -LiteralPath (Join-Path $candidate "blocknew.cfg") -PathType Leaf) {{
                $withCfg += $candidate
            }}
        }}

        if ($withCfg.Count -eq 1) {{
            Save-BlocknewDir $withCfg[0]
            Write-Host "[OK] Auto-detected TongDaXin blocknew directory: $($withCfg[0])" -ForegroundColor Green
            return $withCfg[0]
        }}
        if ($candidates.Count -eq 1) {{
            Save-BlocknewDir $candidates[0]
            Write-Host "[OK] Auto-detected TongDaXin blocknew directory: $($candidates[0])" -ForegroundColor Green
            return $candidates[0]
        }}

        Write-Host ""
        Write-Host "[INFO] Detected multiple TongDaXin blocknew directories:" -ForegroundColor Cyan
        for ($i = 0; $i -lt $candidates.Count; $i++) {{
            $marker = if (Test-Path -LiteralPath (Join-Path $candidates[$i] "blocknew.cfg") -PathType Leaf) {{ " cfg" }} else {{ "" }}
            Write-Host ("  [{0}] {1}{2}" -f ($i + 1), $candidates[$i], $marker)
        }}
        while ($true) {{
            $choice = Read-Host "Choose target directory number (default 1)"
            if (-not $choice) {{ $choice = "1" }}
            $index = 0
            if ([int]::TryParse($choice, [ref]$index) -and $index -ge 1 -and $index -le $candidates.Count) {{
                $selected = $candidates[$index - 1]
                Save-BlocknewDir $selected
                return $selected
            }}
            Write-Host "[ERROR] Invalid selection." -ForegroundColor Red
        }}
    }}

    Write-Host ""
    Write-Host "[!] Could not auto-detect TongDaXin blocknew directory." -ForegroundColor Yellow
    Write-Host "    Please enter the full path to your T0002\\blocknew folder."
    Write-Host "    Example: C:\\new_tdx\\T0002\\blocknew"
    Write-Host ""
    while ($true) {{
        $inputPath = Read-Host "Enter blocknew path"
        $trimmed = Normalize-PathText $inputPath
        if ($trimmed -and (Test-Path -LiteralPath $trimmed -PathType Container)) {{
            $targetDir = $trimmed
            if (-not (Is-BlocknewPath $targetDir)) {{
                Write-Host "[WARN] This does not look like a blocknew folder." -ForegroundColor Yellow
                $choice = Read-Host "Continue anyway? (Y/N)"
                if (-not $choice -or $choice.Trim().ToUpper() -ne "Y") {{ continue }}
            }}
            $targetDir | Out-File $configPath -Force -Encoding utf8
            Write-Host "[OK] Path saved for future use." -ForegroundColor Green
            return $targetDir
        }}
        Write-Host "[ERROR] Path does not exist, please try again." -ForegroundColor Red
    }}
}}

$targetDir = Resolve-BlocknewDir
$cfgPath = Join-Path $targetDir "blocknew.cfg"

$blocks = @(
{ps_blocks_str}
)

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "  AgentTrader - TongDaXin Block Importer"   -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host "Target: $targetDir" -ForegroundColor Green
Write-Host ""

$blkOk = 0
$blkFail = 0
$writtenBlocks = [System.Collections.ArrayList]::new()
foreach ($b in $blocks) {{
    $blkPath = Join-Path $targetDir ($b.Name + ".blk")
    try {{
        $bytes = [System.Convert]::FromBase64String($b.Content)
        [System.IO.File]::WriteAllBytes($blkPath, $bytes)
        Write-Host "  [OK] $($b.Name).blk" -ForegroundColor Cyan
        $blkOk++
        [void]$writtenBlocks.Add($b)
    }} catch {{
        Write-Host "  [FAIL] $($b.Name).blk : $_" -ForegroundColor Red
        $blkFail++
    }}
}}
Write-Host ""

$encoding = Get-TdxEncoding
$cfgFail = $blkFail -gt 0
if ($cfgFail) {{
    Write-Host "  [WARN] Skipping blocknew.cfg update because some .blk files failed." -ForegroundColor Yellow
}}

$existing = [System.Collections.ArrayList]::new()
if (-not $cfgFail) {{
    if (Test-Path $cfgPath) {{
        $cfgBytes = [System.IO.File]::ReadAllBytes($cfgPath)
        if (($cfgBytes.Length % 120) -ne 0) {{
            Write-Host "  [FAIL] blocknew.cfg size is not a multiple of 120 bytes: $($cfgBytes.Length)" -ForegroundColor Red
            $cfgFail = $true
        }} else {{
            for ($i = 0; $i + 120 -le $cfgBytes.Length; $i += 120) {{
                $record = New-Object byte[] 120
                [System.Array]::Copy($cfgBytes, $i, $record, 0, 120)
                [void]$existing.Add([PSCustomObject]@{{
                    Record = $record
                    Abbr   = Get-CfgAbbr $record $encoding
                }})
            }}
            Write-Host "  [OK] Read blocknew.cfg ($($existing.Count) existing blocks)" -ForegroundColor DarkGray
        }}
    }} else {{
        Write-Host "  [INFO] blocknew.cfg not found, will create new." -ForegroundColor DarkGray
    }}
}}

$modified = $false
if (-not $cfgFail) {{
    foreach ($b in $writtenBlocks) {{
        $found = $false
        foreach ($ex in $existing) {{
            if ($ex.Abbr.ToLower() -eq $b.Name.ToLower()) {{
                $found = $true
                break
            }}
        }}
        if (-not $found) {{
            [void]$existing.Add([PSCustomObject]@{{
                Record = New-CfgRecord $b.Name $encoding
                Abbr   = $b.Name
            }})
            $modified = $true
        }}
    }}

    if ($modified) {{
        if (Test-Path $cfgPath) {{
            $backupPath = "$cfgPath.agentrader-$(Get-Date -Format yyyyMMdd-HHmmss).bak"
            Copy-Item -LiteralPath $cfgPath -Destination $backupPath -Force
            Write-Host "  [OK] Backup: $backupPath" -ForegroundColor DarkGray
        }}
        $newBytes = New-Object byte[] ($existing.Count * 120)
        for ($i = 0; $i -lt $existing.Count; $i++) {{
            [System.Array]::Copy($existing[$i].Record, 0, $newBytes, ($i * 120), 120)
        }}
        [System.IO.File]::WriteAllBytes($cfgPath, $newBytes)
        Write-Host "  [OK] blocknew.cfg updated ($($existing.Count) blocks total)" -ForegroundColor Green
    }} else {{
        Write-Host "  [OK] All blocks already registered in blocknew.cfg" -ForegroundColor Yellow
    }}
}}

$verifyFail = $false
if ($blkFail -eq 0 -and -not $cfgFail) {{
    Write-Host ""
    Write-Host "  Verifying written files..." -ForegroundColor DarkGray

    foreach ($b in $writtenBlocks) {{
        $blkPath = Join-Path $targetDir ($b.Name + ".blk")
        if (-not (Test-Path -LiteralPath $blkPath -PathType Leaf)) {{
            Write-Host "  [FAIL] Missing block file after write: $($b.Name).blk" -ForegroundColor Red
            $verifyFail = $true
        }} elseif ((Get-Item -LiteralPath $blkPath).Length -le 0) {{
            Write-Host "  [FAIL] Empty block file after write: $($b.Name).blk" -ForegroundColor Red
            $verifyFail = $true
        }}
    }}

    if (-not (Test-Path -LiteralPath $cfgPath -PathType Leaf)) {{
        Write-Host "  [FAIL] blocknew.cfg was not created or found after update." -ForegroundColor Red
        $verifyFail = $true
    }} else {{
        $verifyCfgBytes = [System.IO.File]::ReadAllBytes($cfgPath)
        if (($verifyCfgBytes.Length % 120) -ne 0) {{
            Write-Host "  [FAIL] blocknew.cfg verify failed: invalid size $($verifyCfgBytes.Length)." -ForegroundColor Red
            $verifyFail = $true
        }} else {{
            $registered = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
            for ($i = 0; $i + 120 -le $verifyCfgBytes.Length; $i += 120) {{
                $record = New-Object byte[] 120
                [System.Array]::Copy($verifyCfgBytes, $i, $record, 0, 120)
                [void]$registered.Add((Get-CfgAbbr $record $encoding))
            }}
            foreach ($b in $writtenBlocks) {{
                if (-not $registered.Contains($b.Name)) {{
                    Write-Host "  [FAIL] blocknew.cfg does not contain block: $($b.Name)" -ForegroundColor Red
                    $verifyFail = $true
                }}
            }}
            if (-not $verifyFail) {{
                Write-Host "  [OK] Verification passed: .blk files exist and blocknew.cfg contains all new block names." -ForegroundColor Green
            }}
        }}
    }}
}}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
if ($blkFail -eq 0 -and -not $cfgFail -and -not $verifyFail) {{
    Write-Host "  Import completed successfully!"         -ForegroundColor Green
    Write-Host "  $blkOk block file(s) written."          -ForegroundColor Green
}} else {{
    Write-Host "  Import completed with errors."          -ForegroundColor Yellow
    Write-Host "  $blkOk succeeded, $blkFail failed."    -ForegroundColor Yellow
}}
Write-Host ""
Write-Host "  IMPORTANT: If TongDaXin is running,"      -ForegroundColor Cyan
Write-Host "  please RESTART it to see new blocks."      -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
if ($blkFail -gt 0 -or $cfgFail -or $verifyFail) {{ exit 1 }}
exit 0
"""

    # Encode the PowerShell script as base64 (UTF-16LE, as required by -EncodedCommand)
    ps_bytes = ps_script.encode("utf-16-le")
    ps_b64 = base64.b64encode(ps_bytes).decode("ascii")

    chunk_size = 7000
    b64_chunks = [ps_b64[i:i + chunk_size] for i in range(0, len(ps_b64), chunk_size)]

    echo_lines = []
    for i, chunk in enumerate(b64_chunks):
        redir = ">" if i == 0 else ">>"
        echo_lines.append(f'{redir} "%TMPPS%" echo {chunk}')
    echo_block = "\n".join(echo_lines)

    bat_script = f"""@echo off
setlocal

REM ============================================================
REM  AgentTrader - TongDaXin Block Import Script
REM  Auto-generated. Double-click to run.
REM ============================================================

set "TMPPS=%TEMP%\\agentrader_import_%RANDOM%_%RANDOM%.b64"
set "TMPPS1=%TEMP%\\agentrader_import_%RANDOM%_%RANDOM%.ps1"
{echo_block}

set "AGENTTRADER_BAT_DIR=%~dp0"
set "AGENTTRADER_PS_B64=%TMPPS%"
set "AGENTTRADER_PS1=%TMPPS1%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$b64=(Get-Content -LiteralPath $env:AGENTTRADER_PS_B64 -Raw) -replace '\\s',''; $bytes=[System.Convert]::FromBase64String($b64); $script=[System.Text.Encoding]::Unicode.GetString($bytes); [System.IO.File]::WriteAllText($env:AGENTTRADER_PS1, $script, [System.Text.Encoding]::Unicode)"
set "PS_STATUS=%ERRORLEVEL%"
if not "%PS_STATUS%"=="0" goto after_powershell

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%TMPPS1%"
set "PS_STATUS=%ERRORLEVEL%"

:after_powershell
del "%TMPPS%" 2>nul
del "%TMPPS1%" 2>nul

if not "%PS_STATUS%"=="0" (
    echo.
    echo [ERROR] Import failed with exit code %PS_STATUS%.
    echo Check the messages above. The window is kept open on purpose.
    echo.
)

echo.
echo Press any key to close this window.
pause >nul
endlocal
exit /b %PS_STATUS%
"""
    return bat_script.replace("\n", "\r\n")
