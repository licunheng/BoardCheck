"""工具启动入口。

可通过以下方式启动:
- `python -m agent` 启动 Web UI(本地浏览器访问)
- `python -m agent --device A02FG --rule-check` 启动规则自检
- `python -m agent --help` 查看所有命令
"""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="板检数据核验工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 启动 Web UI (浏览器自动打开 http://127.0.0.1:5050)
  python -m agent

  # 启动 Web UI 但不自动打开浏览器
  python -m agent --no-browser

  # 指定端口
  python -m agent --port 8080

  # 规则自检 (验证某设备的规则正确率)
  python -m agent --device A02FG --rule-check

  # 命令行核验 (无需 UI,直接处理文件)
  python -m agent --device A02FG \\
    --raw-log path/to/log.txt \\
    --platform-csv path/to/platform.csv \\
    --output-dir ./output
        """,
    )

    parser.add_argument(
        "--port", type=int, default=5050, help="Web UI 端口 (默认 5050)"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Web UI 监听地址 (默认 127.0.0.1)"
    )
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="指定设备型号,用于规则自检或命令行核验",
    )
    parser.add_argument(
        "--rule-check",
        action="store_true",
        help="对指定 --device 设备的规则进行自检",
    )
    parser.add_argument(
        "--raw-log",
        type=Path,
        default=None,
        help="(命令行模式) 板检日志或数据库文件路径",
    )
    parser.add_argument(
        "--platform-csv",
        type=Path,
        default=None,
        help="(命令行模式) 平台导出的 CSV 文件路径",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./output"),
        help="(命令行模式) 报告输出目录 (默认 ./output)",
    )

    args = parser.parse_args()

    # 命令行核验模式
    if args.raw_log or args.platform_csv:
        return _run_cli(args)

    # 规则自检模式
    if args.rule_check:
        from .tools.rule_check import run_rule_check

        if not args.device:
            print("错误: --rule-check 必须配合 --device 使用", file=sys.stderr)
            return 2
        return run_rule_check(args.device)

    # Web UI 模式 (默认)
    from .app import start_web_ui

    start_web_ui(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def _run_cli(args):
    """命令行核验模式,无需启动 UI。"""
    from .core.verifier import run_verification

    if not args.device:
        print("错误: 命令行模式必须指定 --device", file=sys.stderr)
        return 2
    if not args.raw_log or not args.platform_csv:
        print("错误: 命令行模式必须提供 --raw-log 与 --platform-csv", file=sys.stderr)
        return 2

    try:
        result = run_verification(
            device_code=args.device,
            raw_path=args.raw_log,
            platform_csv_path=args.platform_csv,
            output_dir=args.output_dir,
        )
        print(f"✅ 核验完成,报告输出至: {result['output_dir']}")
        print(f"   - 详细对比表: {result['xlsx_path']}")
        print(f"   - 重传 CSV: {result['csv_path']}")
        print(f"   - AI 分析: {result['md_path']}")
        print(f"   - 不一致设备数: {result['inconsistent_count']}/{result['total_count']}")
        return 0 if result["inconsistent_count"] == 0 else 1
    except Exception as e:
        print(f"❌ 核验失败: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())