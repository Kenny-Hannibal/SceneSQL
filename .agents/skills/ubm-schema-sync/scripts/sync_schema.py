#!/usr/bin/env python3
"""
UBM Schema Sync — 追踪数据挖掘项目中 SQLite 注入源变化，同步更新 schema 文件。

用法:
    cd /root/data/text2sql
    python .agents/skills/ubm-schema-sync/scripts/sync_schema.py

环境依赖:
    - DATA_MINING_PROJECT_PATH (从 .env 读取)
    - git
    - PyYAML
"""

import os
import sys
import re
import subprocess
import yaml
from pathlib import Path
from datetime import datetime, timezone

# ------------------------------------------------------------------
# 配置
# ------------------------------------------------------------------
SCHEMA_DIR = Path("agent/backend/app/core")
SCHEMA_FILES = [
    "schema_master_raw.yaml",
    "schema_structure.yaml",
    "schema_dictionary.yaml",
]
ETL_SCRIPT_PATH = Path("agent/backend/app/services/etl/etl_sqlite_to_parquet.py")

# 与 SQLite 注入相关的文件模式（匹配 git diff 返回的路径，含 UBM_mining/ubm_data_mining/ 前缀）
TRACKED_PATTERNS = [
    "UBM_mining/ubm_data_mining/L2_Pred/downstream/ubm/to_sqlite_db.py",
    "UBM_mining/ubm_data_mining/L2_Pred/rule_based_mining/semantic_mining/tokenizer_processor_new.py",
    "UBM_mining/ubm_data_mining/L2_Pred/rule_based_mining/semantic_mining/activity_new/op_*.py",
    "UBM_mining/ubm_data_mining/L2_Pred/rule_based_mining/semantic_mining/activity_new/operator_branch.py",
    "UBM_mining/ubm_data_mining/user_workspace/*/operator_registry.json",
    "UBM_mining/ubm_data_mining/user_workspace/*/*.py",
    "UBM_mining/ubm_data_mining/gsbag_parser/topic_parser/em_behavior_tag_parser.py",
    "UBM_mining/ubm_data_mining/gsbag_parser/tag_map.py",
    "UBM_mining/ubm_data_mining/gsbag_parser/em_parser.py",
    "UBM_mining/ubm_data_mining/mining_pipeline.py",
    # 兼容旧路径（无前缀，相对 ubm_data_mining 子目录）
    "L2_Pred/downstream/ubm/to_sqlite_db.py",
    "L2_Pred/rule_based_mining/semantic_mining/tokenizer_processor_new.py",
    "L2_Pred/rule_based_mining/semantic_mining/activity_new/op_*.py",
    "L2_Pred/rule_based_mining/semantic_mining/activity_new/operator_branch.py",
    "user_workspace/*/operator_registry.json",
    "user_workspace/*/*.py",
    "gsbag_parser/topic_parser/em_behavior_tag_parser.py",
    "gsbag_parser/tag_map.py",
    "gsbag_parser/em_parser.py",
    "mining_pipeline.py",
]

# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------

def load_env():
    """从 .env 加载环境变量（覆盖已存在的环境变量，确保项目配置优先）。"""
    env_path = Path(".env")
    if not env_path.exists():
        return
    with env_path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                os.environ[key] = val.strip()


def get_current_git_hash(repo_path: Path) -> str:
    """获取仓库当前 commit hash（完整）。"""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def get_branch_name(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    branch = result.stdout.strip()
    # detached HEAD 时返回 HEAD，尝试从最近的 commit 的 decorate 信息解析远程分支
    if branch == "HEAD":
        try:
            result2 = subprocess.run(
                ["git", "-C", str(repo_path), "log", "-1", "--oneline", "--decorate"],
                capture_output=True, text=True, check=True,
            )
            # 格式: "hash (HEAD, origin/data_mining/master) subject"
            line = result2.stdout.strip()
            if "origin/" in line:
                # 提取 origin/xxx 部分
                parts = line.split("(")[1].split(")")[0].split(",")
                for p in parts:
                    p = p.strip()
                    if p.startswith("origin/"):
                        branch = p
                        break
        except (subprocess.CalledProcessError, IndexError):
            pass
    return branch


def get_previous_hash(schema_path: Path) -> str | None:
    """从 schema 文件中读取上次记录的 git hash。"""
    try:
        with schema_path.open() as f:
            data = yaml.safe_load(f)
        gv = data.get("git_version", {})
        return gv.get("data_mining_repo")
    except Exception:
        return None


def get_changed_files(repo_path: Path, old_hash: str, new_hash: str) -> list[str]:
    """获取两次 commit 之间发生变更的文件列表。"""
    if old_hash == new_hash:
        return []
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--name-only", f"{old_hash}..{new_hash}"],
        capture_output=True, text=True, check=True,
    )
    return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]


def get_commit_log(repo_path: Path, old_hash: str, new_hash: str) -> list[dict]:
    """获取两次 commit 之间的日志（仅包含与 TRACKED_PATTERNS 相关的 commit）。"""
    if old_hash == new_hash:
        return []

    # 先获取所有变更文件，再筛选出与注入相关的 commit
    all_commits = subprocess.run(
        [
            "git", "-C", str(repo_path), "log",
            f"{old_hash}..{new_hash}",
            "--pretty=format:%H|%s|%ci",
            "--name-only",
        ],
        capture_output=True, text=True, check=True,
    )

    commits = []
    current_commit = None
    current_files = []

    for line in all_commits.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line and len(line.split("|")) == 3:
            # 保存上一个 commit
            if current_commit and current_files:
                filtered = [f for f in current_files if is_tracked(f)]
                if filtered:
                    commits.append({
                        "hash": current_commit["hash"][:12],
                        "full_hash": current_commit["hash"],
                        "subject": current_commit["subject"],
                        "date": current_commit["date"],
                        "files": filtered,
                    })
            parts = line.split("|")
            current_commit = {
                "hash": parts[0],
                "subject": parts[1],
                "date": parts[2],
            }
            current_files = []
        else:
            current_files.append(line)

    # 最后一个
    if current_commit and current_files:
        filtered = [f for f in current_files if is_tracked(f)]
        if filtered:
            commits.append({
                "hash": current_commit["hash"][:12],
                "full_hash": current_commit["hash"],
                "subject": current_commit["subject"],
                "date": current_commit["date"],
                "files": filtered,
            })

    return commits


def is_tracked(filepath: str) -> bool:
    """判断文件路径是否匹配 TRACKED_PATTERNS。"""
    for pattern in TRACKED_PATTERNS:
        # 简单通配符匹配: * 匹配任意字符
        regex = pattern.replace(".", r"\.").replace("*", r".*")
        if re.match(regex + r"$", filepath):
            return True
    return False


def analyze_impact(commits: list[dict]) -> dict:
    """分析 commit 对 schema 的潜在影响。"""
    impact = {
        "new_operators": [],
        "removed_operators": [],
        "modified_operators": [],
        "sqlite_writer_changed": False,
        "tag_map_changed": False,
        "behavior_tag_parser_changed": False,
        "tokenizer_processor_changed": False,
        "user_workspace_changed": False,
        "other": [],
    }

    for commit in commits:
        for f in commit["files"]:
            basename = os.path.basename(f)
            if f == "L2_Pred/downstream/ubm/to_sqlite_db.py":
                impact["sqlite_writer_changed"] = True
            elif f == "L2_Pred/rule_based_mining/semantic_mining/tokenizer_processor_new.py":
                impact["tokenizer_processor_changed"] = True
            elif f == "gsbag_parser/tag_map.py":
                impact["tag_map_changed"] = True
            elif f == "gsbag_parser/topic_parser/em_behavior_tag_parser.py":
                impact["behavior_tag_parser_changed"] = True
            elif f.startswith("user_workspace/"):
                impact["user_workspace_changed"] = True
                if basename.startswith("op_") and basename.endswith(".py"):
                    impact["modified_operators"].append(basename)
                elif basename == "operator_registry.json":
                    impact["modified_operators"].append(f)
            elif basename.startswith("op_") and basename.endswith(".py"):
                impact["modified_operators"].append(basename)
            elif basename == "operator_branch.py":
                impact["other"].append(f"基类变更: {basename}")
            else:
                impact["other"].append(f)

    # 去重
    for key in ["new_operators", "removed_operators", "modified_operators", "other"]:
        impact[key] = sorted(set(impact[key]))

    return impact


def generate_report(commits: list[dict], impact: dict, old_hash: str, new_hash: str, etl_sync: dict | None = None) -> str:
    """生成 Markdown 格式的变更报告。"""
    lines = [
        "# Schema 同步报告",
        "",
        f"- 上一版本: `{old_hash[:12] if old_hash else 'N/A'}`",
        f"- 当前版本: `{new_hash[:12]}`",
        f"- 生成时间: {datetime.now(timezone.utc).astimezone().isoformat()}",
        "",
        "## 相关 Commit 日志",
        "",
    ]

    if not commits:
        lines.append("_无相关变更。_")
    else:
        for c in commits:
            lines.append(f"### {c['hash']} — {c['subject']}")
            lines.append(f"- 日期: {c['date']}")
            lines.append("- 变更文件:")
            for f in c["files"]:
                lines.append(f"  - `{f}`")
            lines.append("")

    lines.extend([
        "## 影响分析",
        "",
    ])

    if impact["sqlite_writer_changed"]:
        lines.append("- ⚠️ **to_sqlite_db.py 发生变更** — 可能影响标签过滤逻辑、表结构或写入规则")
    if impact["tokenizer_processor_changed"]:
        lines.append("- ⚠️ **tokenizer_processor_new.py 发生变更** — 可能新增/删除/修改算子注册")
    if impact["tag_map_changed"]:
        lines.append("- ⚠️ **tag_map.py 发生变更** — 车端行为标签映射可能有新增")
    if impact["behavior_tag_parser_changed"]:
        lines.append("- ⚠️ **em_behavior_tag_parser.py 发生变更** — 车端标签解析逻辑可能有变")
    if impact["user_workspace_changed"]:
        lines.append("- ⚠️ **user_workspace 发生变更** — 自定义算子可能有新增或修改")
    if impact["modified_operators"]:
        lines.append(f"- 📁 **算子文件变更** ({len(impact['modified_operators'])} 个):")
        for op in impact["modified_operators"]:
            lines.append(f"  - `{op}`")
    if impact["other"]:
        lines.append(f"- 📁 **其他相关变更** ({len(impact['other'])} 项):")
        for o in impact["other"]:
            lines.append(f"  - `{o}`")

    if not any([
        impact["sqlite_writer_changed"],
        impact["tokenizer_processor_changed"],
        impact["tag_map_changed"],
        impact["behavior_tag_parser_changed"],
        impact["user_workspace_changed"],
        impact["modified_operators"],
        impact["other"],
    ]):
        lines.append("_未发现对 SQLite 注入源有影响的变更。_")

    # ---- ETL CORE_TABLES 同步状态 ----
    if etl_sync is None:
        etl_sync = {"changed": False, "added": [], "removed": []}
    lines.extend([
        "",
        "## ETL 同步状态",
        "",
    ])
    if etl_sync["changed"]:
        lines.append(f"- ✅ **CORE_TABLES 已自动同步** (`{ETL_SCRIPT_PATH}`)")
        if etl_sync["added"]:
            lines.append(f"  - 新增表: {', '.join(etl_sync['added'])}")
        if etl_sync["removed"]:
            lines.append(f"  - 移除表: {', '.join(etl_sync['removed'])}")
    else:
        lines.append("- ✅ CORE_TABLES 已与 schema_structure.yaml 保持一致，无需修改。")
    lines.append("")

    lines.extend([
        "## 建议操作",
        "",
        "1. 查看上方变更文件，确认是否有新的 `add_event()` 调用或 `add_table()` 调用。",
        "2. 如有新增算子，确认其 `label_id` 值并补充到 schema 的 `range_tag.enum` 中。",
        "3. 如有新增表结构（`to_sqlite_db.py` 中新增 `CREATE TABLE`），同步更新 `schema_structure.yaml`，并确认 `CORE_TABLES` 已自动同步。",
        "4. 如有新增车端标签，确认 `tag_map.py` 中的映射并补充。",
        "5. 更新 `schema_dictionary.yaml` 中对应标签/字段的定义。",
        "6. 运行本脚本更新 schema 文件中的 `git_version`。",
        "",
    ])

    return "\n".join(lines)


def get_schema_tables() -> list[str]:
    """从 schema_structure.yaml 中提取当前定义的表名列表（保持 YAML 中的顺序）。"""
    schema_path = Path(SCHEMA_DIR) / "schema_structure.yaml"
    if not schema_path.exists():
        return []
    with schema_path.open() as f:
        data = yaml.safe_load(f)
    tables = data.get("database_schema", {}).get("tables", [])
    return [t["name"] for t in tables if "name" in t]


def get_etl_core_tables() -> list[str]:
    """从 ETL 脚本中提取当前的 CORE_TABLES 列表。"""
    etl_path = Path(ETL_SCRIPT_PATH)
    if not etl_path.exists():
        return []
    with etl_path.open() as f:
        content = f.read()
    match = re.search(r'CORE_TABLES\s*=\s*\[(.*?)\]', content, re.DOTALL)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def sync_etl_core_tables() -> dict:
    """同步 ETL 脚本的 CORE_TABLES 与 schema_structure.yaml 保持一致。

    Returns:
        {"changed": bool, "old": list, "new": list, "added": list, "removed": list}
    """
    schema_tables = get_schema_tables()
    old_tables = get_etl_core_tables()

    added = [t for t in schema_tables if t not in old_tables]
    removed = [t for t in old_tables if t not in schema_tables]

    if not added and not removed:
        return {"changed": False, "old": old_tables, "new": schema_tables, "added": [], "removed": []}

    etl_path = Path(ETL_SCRIPT_PATH)
    with etl_path.open() as f:
        content = f.read()

    # 生成新的列表文本（保持 schema 中的定义顺序）
    lines = ["CORE_TABLES = ["]
    for t in schema_tables:
        lines.append(f'    "{t}",')
    lines.append("]")
    new_block = "\n".join(lines)

    new_content = re.sub(
        r'CORE_TABLES\s*=\s*\[.*?\]',
        new_block,
        content,
        flags=re.DOTALL,
    )

    with etl_path.open("w") as f:
        f.write(new_content)

    return {"changed": True, "old": old_tables, "new": schema_tables, "added": added, "removed": removed}


def update_schema_git_versions(repo_path: Path, new_hash: str, branch: str):
    """更新所有 schema 文件中的 git_version 字段。"""
    now = datetime.now(timezone.utc).astimezone().isoformat()
    for fname in SCHEMA_FILES:
        fpath = Path(SCHEMA_DIR) / fname
        if not fpath.exists():
            print(f"[WARN] 未找到 {fpath}，跳过")
            continue

        with fpath.open() as f:
            data = yaml.safe_load(f)

        data["git_version"] = {
            "data_mining_repo": new_hash,
            "branch": branch,
            "synced_at": now,
            "note": "数据挖掘项目当前 commit hash，schema 以此版本为基准",
        }

        with fpath.open("w") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False, width=200)

        print(f"[OK] 已更新 {fpath}")


def main():
    load_env()

    repo_path_str = os.environ.get("DATA_MINING_PROJECT_PATH")

    if not repo_path_str:
        print("[ERROR] 环境变量 DATA_MINING_PROJECT_PATH 未设置，请在 .env 中配置")
        sys.exit(1)

    repo_path = Path(repo_path_str)
    # Use git itself to verify repo validity (handles symlink + submodule cases)
    try:
        subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--git-dir"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        print(f"[ERROR] {repo_path} 不是 git 仓库")
        sys.exit(1)

    # 获取当前 hash
    new_hash = get_current_git_hash(repo_path)
    branch = get_branch_name(repo_path)
    print(f"[INFO] 数据挖掘项目: {repo_path}")
    print(f"[INFO] 当前分支: {branch}")
    print(f"[INFO] 当前 commit: {new_hash[:12]}")

    # 获取上一版本 hash（从 schema_master_raw.yaml 读取）
    master_schema = Path(SCHEMA_DIR) / SCHEMA_FILES[0]
    old_hash = get_previous_hash(master_schema)
    print(f"[INFO] 上一版本 commit: {old_hash[:12] if old_hash else 'N/A'}")

    # 无论 git hash 是否变化，都先同步 ETL CORE_TABLES（schema_structure.yaml 可能被手动修改）
    etl_sync = sync_etl_core_tables()
    if etl_sync["changed"]:
        print(f"[OK] CORE_TABLES 已自动同步: {ETL_SCRIPT_PATH}")
        if etl_sync["added"]:
            print(f"    新增表: {', '.join(etl_sync['added'])}")
        if etl_sync["removed"]:
            print(f"    移除表: {', '.join(etl_sync['removed'])}")

    if old_hash == new_hash:
        print("[INFO] schema 已与最新代码同步，无需更新")
        # 仍然更新 synced_at 时间戳
        update_schema_git_versions(repo_path, new_hash, branch)
        sys.exit(0)

    # 获取相关 commit
    print(f"[INFO] 分析 {old_hash[:12]}..{new_hash[:12]} 的变更...")
    commits = get_commit_log(repo_path, old_hash, new_hash)
    impact = analyze_impact(commits)

    # 生成报告
    report = generate_report(commits, impact, old_hash, new_hash, etl_sync=etl_sync)
    report_path = Path("/tmp/schema_sync_report.md")
    with report_path.open("w") as f:
        f.write(report)
    print(f"[INFO] 报告已保存到: {report_path}")

    # 打印摘要
    print("\n" + "=" * 60)
    print(report)
    print("=" * 60 + "\n")

    # 询问是否更新
    if os.environ.get("AUTO_UPDATE_SCHEMA", "").lower() in ("1", "true", "yes"):
        confirm = "y"
    else:
        confirm = input("是否更新 schema 文件中的 git_version? [y/N]: ").strip().lower()

    if confirm in ("y", "yes"):
        update_schema_git_versions(repo_path, new_hash, branch)
        print("[OK] 同步完成")
    else:
        print("[INFO] 已取消更新")


if __name__ == "__main__":
    main()
