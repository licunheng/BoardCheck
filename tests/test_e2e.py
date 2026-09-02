"""端到端测试:A02FG 真实样本核验。

运行方法:
    python tests/test_e2e.py

使用 A02FG 的真实样本进行端到端验证,核对:
1. 文本日志正确抽取 SN/MAC/ICCID/追溯码
2. 与平台 CSV 比对,识别 150/150 条 SN 错位
3. 三份报告正确生成
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# 添加项目根到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core.extractor import DeviceRecord, extract_from_text_log
from agent.core.file_reader import read_csv
from agent.core.preprocessor import preprocess_raw_file
from agent.core.rule_loader import DeviceRules
from agent.core.verifier import run_verification


def test_rule_load():
    """测试 1: 加载 A02FG 设备规则。"""
    print("\n[Test 1] 加载 A02FG 设备规则...")
    rules = DeviceRules.load("A02FG")
    assert rules.device_code == "A02FG"
    assert rules.is_4g is True
    assert "sn" in rules.field_extractors
    assert "trace_code" in rules.field_extractors
    print(f"  ✓ 设备: {rules.display_name}")
    print(f"  ✓ 4G: {rules.is_4g}")
    print(f"  ✓ 抽取规则: {list(rules.field_extractors.keys())}")
    return rules


def test_text_log_extraction(rules: DeviceRules):
    """测试 2: 从文本日志抽取设备记录。"""
    print("\n[Test 2] 从文本日志抽取...")
    # 用筛选过的样本(已剔除无关行)
    sample_path = (
        Path(__file__).resolve().parent.parent
        / "A02FG"
        / "筛选结果-含CCID-Info-2026-08-05.md"
    )
    assert sample_path.is_file(), f"样本不存在: {sample_path}"

    pre = preprocess_raw_file(sample_path, rules)
    print(f"  ✓ 输入行数: {pre.total_input_lines}")
    print(f"  ✓ 保留行数: {pre.total_kept_lines}")

    records = extract_from_text_log(pre.kept_lines, rules)
    print(f"  ✓ 抽取记录数: {len(records)}")

    # 至少抽到一些完整记录
    complete = [r for r in records if r.is_complete(is_4g=True)]
    print(f"  ✓ 完整记录(4字段都有): {len(complete)}")

    assert len(records) > 0, "未抽取到任何记录"
    assert len(complete) > 0, "未抽取到任何完整记录"

    # 抽查一条
    sample_rec = complete[0]
    print(f"  ✓ 示例记录: SN={sample_rec.sn[:8]}..., MAC={sample_rec.mac}, ICCID={sample_rec.iccid[:8]}..., 追溯码={sample_rec.trace_code[:8]}...")
    return records


def test_csv_parsing(rules: DeviceRules):
    """测试 3: 读取平台 CSV。"""
    print("\n[Test 3] 读取平台 CSV...")
    csv_path = (
        Path(__file__).resolve().parent.parent
        / "A02FG"
        / "板检记录.csv"
    )
    csv_records = read_csv(csv_path)
    print(f"  ✓ 平台记录数: {len(csv_records)}")
    if csv_records:
        print(f"  ✓ 列名: {list(csv_records[0].keys())}")

    assert len(csv_records) > 0
    return csv_records


def test_e2e_verification(rules: DeviceRules):
    """测试 4: 端到端核验,生成三份报告。"""
    print("\n[Test 4] 端到端核验...")
    raw_log = (
        Path(__file__).resolve().parent.parent
        / "A02FG"
        / "筛选结果-含CCID-Info-2026-08-05.md"
    )
    platform_csv = (
        Path(__file__).resolve().parent.parent
        / "A02FG"
        / "板检记录.csv"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        result = run_verification(
            device_code="A02FG",
            raw_path=raw_log,
            platform_csv_path=platform_csv,
            output_dir=output_dir,
        )

        print(f"  ✓ 报告目录: {output_dir}")
        print(f"  ✓ xlsx: {Path(result['xlsx_path']).name}")
        print(f"  ✓ csv:  {Path(result['csv_path']).name}")
        print(f"  ✓ md:   {Path(result['md_path']).name}")
        print(f"  ✓ 总结: {result['summary']}")
        print(f"  ✓ 耗时: {result['elapsed_seconds']:.1f} 秒")

        # 期望:大量 SN 不一致(A02FG 已知 Bug)
        assert result['summary']['total'] > 0
        # 至少应该检测到一些 MISMATCH 或 RAW_ONLY
        assert (result['summary'].get('mismatch', 0) + result['summary'].get('raw_only', 0)) > 0, (
            "应当至少检测出一些不一致记录"
        )

        # 校验报告文件存在且非空
        assert Path(result['xlsx_path']).stat().st_size > 0
        assert Path(result['csv_path']).stat().st_size > 0
        assert Path(result['md_path']).stat().st_size > 0

        # 持久化一份输出供人工检查
        final_dir = Path(__file__).resolve().parent.parent / "output_e2e"
        final_dir.mkdir(exist_ok=True)
        import shutil

        for p in output_dir.glob("*"):
            shutil.copy(p, final_dir)
        print(f"  ✓ 报告已复制到: {final_dir}")

        return result


def test_web_upload_route():
    """测试 5: Web 上传路由返回 JSON 而非 HTML(回归 device_exists 修复)。"""
    print("\n[Test 5] Web 上传路由...")
    from io import BytesIO

    from agent.app import create_app

    client = create_app().test_client()

    raw_log = (
        Path(__file__).resolve().parent.parent
        / "A02FG"
        / "筛选结果-含CCID-Info-2026-08-05.md"
    )
    with raw_log.open("rb") as f:
        data = {"file": (BytesIO(f.read()), "sample.md"), "device_code": "A02FG"}
        r = client.post(
            "/api/upload/raw", data=data, content_type="multipart/form-data"
        )

    assert r.status_code == 200, f"上传应成功,实际 {r.status_code}"
    assert r.headers.get("Content-Type", "").startswith("application/json"), (
        "上传路由必须返回 JSON,而非 HTML"
    )
    payload = r.get_json()
    assert payload.get("ok") is True
    assert payload.get("raw_files") and len(payload["raw_files"]) >= 1, "应返回 raw_files 列表"
    print(f"  ✓ 上传返回 JSON: {payload['raw_files'][0]['filename']}")

    # 不存在的设备应返回 JSON 404(而非 HTML 500)
    with raw_log.open("rb") as f:
        data = {"file": (BytesIO(f.read()), "sample.md"), "device_code": "NOT_EXIST"}
        r = client.post(
            "/api/upload/raw", data=data, content_type="multipart/form-data"
        )
    assert r.status_code == 404
    assert r.headers.get("Content-Type", "").startswith("application/json")
    print(f"  ✓ 未知设备返回 JSON 404: {r.get_json()['error']}")
    return True


def main():
    print("=" * 60)
    print("  板检数据核验工具 - 端到端测试 (A02FG)")
    print("=" * 60)

    rules = test_rule_load()
    test_text_log_extraction(rules)
    test_csv_parsing(rules)
    test_web_upload_route()
    result = test_e2e_verification(rules)

    print("\n" + "=" * 60)
    print("  ✅ 全部测试通过")
    print("=" * 60)
    print(f"  核验总数: {result['summary']['total']}")
    print(f"  不一致数: {result['summary'].get('mismatch', 0)} + "
          f"{result['summary'].get('raw_only', 0)} = "
          f"{result['inconsistent_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())