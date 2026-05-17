"""
Background runner for the local workbench.

The Streamlit app starts this module as a detached process. The runner writes
run.log and run_state.json, so UI reruns or page switches do not lose process
state.
"""
from __future__ import annotations

import datetime as dt
import codecs
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def resolved_env() -> dict[str, str]:
    env = os.environ.copy()
    if env.get("TS_TOKEN") and not env.get("TUSHARE_TOKEN"):
        env["TUSHARE_TOKEN"] = env["TS_TOKEN"]
    env.setdefault("NO_COLOR", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def append_log(log_path: Path, text: str) -> None:
    with open(log_path, "a", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()


def run(run_dir: Path) -> int:
    config = load_json(run_dir / "run_config.json")
    commands = config.get("commands", [])
    log_path = run_dir / "run.log"
    state_path = run_dir / "run_state.json"
    owner = str(config.get("owner") or "run_center")
    owner_label = str(config.get("owner_label") or "运行中心")

    log_path.write_text(
        f"[System] 运行快照: {run_dir}\n"
        f"[System] 运行模式: {config.get('run_mode', '')}\n",
        encoding="utf-8",
    )
    write_json(
        state_path,
        {
            "status": "running",
            "owner": owner,
            "owner_label": owner_label,
            "runner_pid": os.getpid(),
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
            "run_dir": str(run_dir),
        },
    )

    for index, step in enumerate(commands, 1):
        step_name = str(step["name"])
        cmd = [str(part) for part in step["cmd"]]
        append_log(log_path, f"\n[Step] {step_name}\n[Command] {' '.join(cmd)}\n")
        write_json(
            state_path,
            {
                "status": "running",
                "owner": owner,
                "owner_label": owner_label,
                "runner_pid": os.getpid(),
                "current_step": step_name,
                "step_index": index,
                "step_total": len(commands),
                "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
                "run_dir": str(run_dir),
            },
        )

        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=resolved_env(),
        )
        write_json(
            state_path,
            {
                "status": "running",
                "owner": owner,
                "owner_label": owner_label,
                "runner_pid": os.getpid(),
                "child_pid": proc.pid,
                "current_step": step_name,
                "step_index": index,
                "step_total": len(commands),
                "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
                "run_dir": str(run_dir),
            },
        )
        assert proc.stdout is not None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = proc.stdout.read(1)
            if chunk == b"" and proc.poll() is not None:
                break
            if chunk:
                append_log(log_path, decoder.decode(chunk))
        tail = decoder.decode(b"", final=True)
        if tail:
            append_log(log_path, tail)

        return_code = proc.wait()
        if return_code != 0:
            append_log(log_path, f"\n[ERROR] {step_name} 失败，退出码 {return_code}\n")
            write_json(
                state_path,
                {
                    "status": "failed",
                    "owner": owner,
                    "owner_label": owner_label,
                    "runner_pid": os.getpid(),
                    "current_step": step_name,
                    "return_code": return_code,
                    "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "run_dir": str(run_dir),
                },
            )
            return return_code
        append_log(log_path, f"\n[OK] {step_name} 完成\n")

    append_log(log_path, "\n[SUCCESS] 流程执行完毕\n")
    write_json(
        state_path,
        {
            "status": "success",
            "owner": owner,
            "owner_label": owner_label,
            "runner_pid": os.getpid(),
            "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
            "run_dir": str(run_dir),
        },
    )
    return 0


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m workbench.runner <run_dir>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(run(Path(sys.argv[1]).resolve()))


if __name__ == "__main__":
    main()
