"""Flask Web UI - 板检数据核验 Agent。

提供轻量级按钮界面:
1. 导入板检数据 (txt / db,支持多文件)
2. 导入平台 CSV (支持多文件)
3. 开始核验
4. 下载报告(含历史记录,刷新不丢失)

界面完全本地化运行,不向外部网络泄露任何数据(LLM 调用除外)。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request, send_from_directory

from .config import (
    LLMConfig,
    get_config,
    get_storage_dirs,
    load_review_settings,
    save_review_settings,
    save_storage_dirs,
    update_llm_config,
)
from .core.device_generator import DeviceConfigError, generate_device_config
from .core.rule_loader import DeviceRules, device_exists, list_supported_devices
from .core.verifier import run_verification

logger = logging.getLogger(__name__)

# === 后台任务注册表(设备配置生成,LLM 慢任务放后台跑,前端轮询进度) ===
_generate_jobs: Dict[str, Dict[str, Any]] = {}
_generate_jobs_lock = threading.Lock()

# === 核验后台任务注册表(核验可能含 LLM 复核,同样放后台跑) ===
_verify_jobs: Dict[str, Dict[str, Any]] = {}
_verify_jobs_lock = threading.Lock()


def _set_job(job_id: str, **kw) -> None:
    with _generate_jobs_lock:
        _generate_jobs.setdefault(job_id, {}).update(kw)


def _set_verify_job(job_id: str, **kw) -> None:
    with _verify_jobs_lock:
        _verify_jobs.setdefault(job_id, {}).update(kw)


def create_app() -> Flask:
    """创建 Flask 应用实例。"""
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "static"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB
    app.secret_key = os.environ.get("AGENT_SECRET", "agent-dev-secret")

    # 存储目录(工程内默认,可用界面配置并持久化)
    upload_dir, output_dir = get_storage_dirs()
    upload_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    app.config["UPLOAD_DIR"] = upload_dir
    app.config["OUTPUT_DIR"] = output_dir

    # 全局状态(当前批次,内存;历史记录走磁盘扫描)
    state: Dict[str, Any] = {
        "raw_paths": [],
        "raw_files": [],
        "platform_paths": [],
        "platform_files": [],
        "device_code": None,
        "last_result": None,
        "last_error": None,
    }

    # === 统一 JSON 错误响应(避免前端拿到 HTML 报错页) ===

    from werkzeug.exceptions import HTTPException

    @app.errorhandler(HTTPException)
    def _handle_http_error(e):
        if e.code == 413:
            msg = "文件过大(超过 200MB 限制)"
        elif e.code == 404:
            msg = "接口不存在或路径错误"
        else:
            msg = e.description or e.name
        return jsonify(error=msg), e.code

    @app.errorhandler(Exception)
    def _handle_unhandled(e):
        logger.exception("未处理异常: %s", getattr(e, "__class__", "").__name__)
        return jsonify(error=str(e) or "服务器内部错误"), 500

    # === 路由 ===

    @app.route("/")
    def index():
        devices = list_supported_devices()
        return render_template("index.html", devices=devices)

    @app.route("/api/devices")
    def api_devices():
        devices = []
        for code in list_supported_devices():
            try:
                rules = DeviceRules.load(code)
                devices.append(
                    {
                        "code": rules.device_code,
                        "name": rules.display_name,
                        "is_4g": rules.is_4g,
                        "version": rules.version,
                    }
                )
            except Exception as e:
                logger.warning("加载设备 %s 失败: %s", code, e)
        return jsonify(devices=devices)

    # === 上传(支持多文件) ===

    def _saved_files() -> List:
        """从请求中取出 files(多文件)或 file(单文件),统一为列表。"""
        files = request.files.getlist("files")
        if not files:
            files = request.files.getlist("file")
        return files

    @app.route("/api/upload/raw", methods=["POST"])
    def api_upload_raw():
        files = _saved_files()
        if not files:
            return jsonify(error="未提供文件"), 400
        device_code = request.form.get("device_code")
        if not device_code:
            return jsonify(error="必须指定 device_code"), 400
        if not device_exists(device_code):
            return jsonify(error=f"设备 {device_code!r} 不存在"), 404

        upload_dir = Path(app.config["UPLOAD_DIR"])
        upload_dir.mkdir(parents=True, exist_ok=True)

        saved_paths: List[Path] = []
        meta: List[Dict[str, Any]] = []
        for f in files:
            if not f or not f.filename:
                continue
            saved_path = upload_dir / f"{uuid.uuid4().hex}_{Path(f.filename).name}"
            f.save(saved_path)
            saved_paths.append(saved_path)
            meta.append(
                {"filename": Path(f.filename).name, "size_bytes": saved_path.stat().st_size}
            )

        if not saved_paths:
            return jsonify(error="未保存任何有效文件"), 400

        # 追加到当前批次(支持分多次上传后一起核验)
        state["raw_paths"].extend(saved_paths)
        state["raw_files"].extend(meta)
        state["device_code"] = device_code
        return jsonify(
            ok=True,
            count=len(saved_paths),
            raw_files=state["raw_files"],
            device_code=device_code,
        )

    @app.route("/api/upload/platform", methods=["POST"])
    def api_upload_platform():
        files = _saved_files()
        if not files:
            return jsonify(error="未提供文件"), 400

        upload_dir = Path(app.config["UPLOAD_DIR"])
        upload_dir.mkdir(parents=True, exist_ok=True)

        saved_paths: List[Path] = []
        meta: List[Dict[str, Any]] = []
        for f in files:
            if not f or not f.filename:
                continue
            saved_path = upload_dir / f"{uuid.uuid4().hex}_{Path(f.filename).name}"
            f.save(saved_path)
            saved_paths.append(saved_path)
            meta.append(
                {"filename": Path(f.filename).name, "size_bytes": saved_path.stat().st_size}
            )

        if not saved_paths:
            return jsonify(error="未保存任何有效文件"), 400

        state["platform_paths"].extend(saved_paths)
        state["platform_files"].extend(meta)
        return jsonify(ok=True, count=len(saved_paths), platform_files=state["platform_files"])

    # === 上传删除(板检记录 / 平台记录均可删) ===

    def _delete_upload(kind: str, data: Dict[str, Any]):
        key_paths = kind + "_paths"
        key_files = kind + "_files"
        raw_index = data.get("index")

        # index 为空/None → 清空全部
        if raw_index is None or raw_index == "":
            state[key_paths] = []
            state[key_files] = []
            return jsonify(ok=True, cleared=True, count=0, files=[])

        try:
            idx = int(raw_index)
        except (TypeError, ValueError):
            return jsonify(error="无效的 index"), 400

        paths = state[key_paths]
        files = state[key_files]
        if idx < 0 or idx >= len(paths):
            return jsonify(error="index 越界"), 400
        paths.pop(idx)
        files.pop(idx)
        return jsonify(ok=True, count=len(paths), files=files)

    @app.route("/api/upload/raw", methods=["DELETE"])
    def api_delete_raw():
        data = request.get_json(silent=True) or {}
        return _delete_upload("raw", data)

    @app.route("/api/upload/platform", methods=["DELETE"])
    def api_delete_platform():
        data = request.get_json(silent=True) or {}
        return _delete_upload("platform", data)

    # === 核验 ===

    @app.route("/api/verify", methods=["POST"])
    def api_verify():
        if not state["raw_paths"]:
            return jsonify(error="请先导入板检数据"), 400
        if not state["platform_paths"]:
            return jsonify(error="请先导入平台 CSV"), 400
        if not state["device_code"]:
            return jsonify(error="未指定设备型号"), 400

        # 读取并持久化 LLM 复核开关(人工可控)
        try:
            data = request.get_json(force=True) or {}
        except Exception:
            data = {}
        review = save_review_settings(
            llm_review=bool(data.get("llm_review", False)),
            independent_extraction=bool(data.get("independent_extraction", False)),
            comparison_review=bool(data.get("comparison_review", True)),
        )

        output_dir = Path(app.config["OUTPUT_DIR"])
        output_dir.mkdir(parents=True, exist_ok=True)

        job_id = uuid.uuid4().hex
        _set_verify_job(
            job_id,
            status="running",
            message="任务已提交…",
            result=None,
            error=None,
            started_at=time.time(),
            finished_at=None,
        )

        def _run():
            try:
                result = run_verification(
                    device_code=state["device_code"],
                    raw_path=list(state["raw_paths"]),
                    platform_csv_path=list(state["platform_paths"]),
                    output_dir=output_dir,
                    llm_review=review["llm_review"],
                    independent_extraction=review["independent_extraction"],
                    comparison_review=review["comparison_review"],
                    progress_callback=lambda msg: _set_verify_job(job_id, message=msg),
                )
                _set_verify_job(
                    job_id, status="done", message="核验完成", result=result, finished_at=time.time()
                )
            except Exception as e:
                logger.exception("核验失败")
                _set_verify_job(
                    job_id, status="error", message="核验失败", error=str(e), finished_at=time.time()
                )

        threading.Thread(target=_run, daemon=True).start()
        return jsonify(ok=True, job_id=job_id)

    @app.route("/api/verify/status")
    def api_verify_status():
        job_id = request.args.get("job_id", "")
        with _verify_jobs_lock:
            job = _verify_jobs.get(job_id)
        if not job:
            return jsonify(ok=False, error="任务不存在或已过期"), 404
        return jsonify(ok=True, job=job)

    # === LLM 复核开关 ===

    @app.route("/api/review/settings")
    def api_review_settings():
        return jsonify(review=load_review_settings())

    @app.route("/api/review/settings", methods=["POST"])
    def api_save_review_settings():
        try:
            data = request.get_json(force=True) or {}
        except Exception:
            data = {}
        review = save_review_settings(
            llm_review=bool(data.get("llm_review", False)),
            independent_extraction=bool(data.get("independent_extraction", False)),
            comparison_review=bool(data.get("comparison_review", True)),
        )
        return jsonify(ok=True, review=review)

    # === 下载与历史 ===

    @app.route("/api/download/<path:filename>")
    def api_download(filename):
        output_dir = Path(app.config["OUTPUT_DIR"])
        if not output_dir.is_dir():
            return jsonify(error="输出目录不存在"), 404
        return send_from_directory(str(output_dir), filename, as_attachment=True)

    @app.route("/api/history")
    def api_history():
        output_dir = Path(app.config["OUTPUT_DIR"])
        return jsonify(history=_list_history(output_dir))

    @app.route("/api/status")
    def api_status():
        return jsonify(
            device_code=state["device_code"],
            raw_files=state["raw_files"],
            platform_files=state["platform_files"],
            last_result=state["last_result"],
            last_error=state["last_error"],
        )

    # === 存储设置 ===

    @app.route("/api/storage")
    def api_storage():
        upload_dir, output_dir = get_storage_dirs()
        return jsonify(upload_dir=str(upload_dir), output_dir=str(output_dir))

    @app.route("/api/storage", methods=["POST"])
    def api_save_storage():
        try:
            data = request.get_json(force=True) or {}
        except Exception:
            data = {}
        # 空字符串表示"重置为工程内默认",因此这里不把空串转成 None
        upload = (data.get("upload_dir") or "").strip()
        output = (data.get("output_dir") or "").strip()
        try:
            u, o = save_storage_dirs(upload_dir=upload, output_dir=output)
            u.mkdir(parents=True, exist_ok=True)
            o.mkdir(parents=True, exist_ok=True)
            app.config["UPLOAD_DIR"] = u
            app.config["OUTPUT_DIR"] = o
            return jsonify(ok=True, upload_dir=str(u), output_dir=str(o))
        except Exception as e:
            logger.exception("保存存储设置失败")
            return jsonify(ok=False, error=str(e)), 500

    # === 设备配置生成(提示词输入 → LLM 自动生成,后台任务) ===

    @app.route("/api/devices/generate", methods=["POST"])
    def api_generate_device():
        try:
            data = request.get_json(force=True) or {}
        except Exception:
            data = {}
        device_code = (data.get("device_code") or "").strip()
        description = (data.get("description") or "").strip()
        sample_log = data.get("sample_log") or ""
        sample_csv = data.get("sample_csv") or ""
        overwrite = bool(data.get("overwrite"))

        is_4g_hint = data.get("is_4g_hint")
        if is_4g_hint in ("", None):
            is_4g_hint = None
        else:
            is_4g_hint = bool(is_4g_hint)

        # 快速预校验(输入错误立即返回,不占后台任务)
        if not device_code:
            return jsonify(ok=False, error="设备代号不能为空"), 400
        if not description:
            return jsonify(ok=False, error="请提供该产品板检规则的自然语言描述"), 400
        if device_exists(device_code) and not overwrite:
            return jsonify(
                ok=False, error=f"设备 {device_code!r} 已存在。如需覆盖,请勾选「覆盖已有配置」。"
            ), 400

        job_id = uuid.uuid4().hex
        _set_job(
            job_id,
            status="running",
            message="任务已提交,准备调用大模型...",
            result=None,
            error=None,
            started_at=time.time(),
            finished_at=None,
        )

        def _run():
            try:
                result = generate_device_config(
                    device_code=device_code,
                    description=description,
                    sample_log=sample_log,
                    sample_csv=sample_csv,
                    is_4g_hint=is_4g_hint,
                    overwrite=overwrite,
                    progress_callback=lambda msg: _set_job(job_id, message=msg),
                )
                _set_job(job_id, status="done", message="生成完成", result=result, finished_at=time.time())
            except DeviceConfigError as e:
                _set_job(job_id, status="error", message="生成失败", error=str(e), finished_at=time.time())
            except Exception as e:
                logger.exception("生成设备配置失败")
                _set_job(job_id, status="error", message="生成失败", error=str(e), finished_at=time.time())

        threading.Thread(target=_run, daemon=True).start()
        return jsonify(ok=True, job_id=job_id)

    @app.route("/api/devices/generate/status")
    def api_generate_status():
        job_id = request.args.get("job_id", "")
        with _generate_jobs_lock:
            job = _generate_jobs.get(job_id)
        if not job:
            return jsonify(ok=False, error="任务不存在或已过期"), 404
        return jsonify(ok=True, job=job)

    # === LLM 设置 ===

    def _llm_payload(cfg: LLMConfig) -> Dict[str, Any]:
        return {
            "provider": cfg.provider,
            "model": cfg.model,
            "api_key_set": bool(cfg.api_key),
            "api_key_hint": cfg.api_key[-4:] if cfg.api_key else "",
            "base_url": cfg.base_url or "",
        }

    @app.route("/api/settings")
    def api_settings():
        return jsonify(llm=_llm_payload(get_config().llm))

    @app.route("/api/settings", methods=["POST"])
    def api_save_settings():
        try:
            data = request.get_json(force=True) or {}
        except Exception:
            data = {}
        llm = data.get("llm") or {}
        api_key = (llm.get("api_key") or "").strip()
        try:
            new_cfg = update_llm_config(
                provider=(llm.get("provider") or "").strip() or None,
                model=(llm.get("model") or "").strip() or None,
                api_key=api_key or None,  # 空串保持不变
                base_url=llm.get("base_url") if "base_url" in llm else None,
            )
            return jsonify(ok=True, llm=_llm_payload(new_cfg))
        except Exception as e:
            logger.exception("保存设置失败")
            return jsonify(ok=False, error=str(e)), 500

    @app.route("/api/settings/test", methods=["POST"])
    def api_test_settings():
        try:
            data = request.get_json(force=True) or {}
        except Exception:
            data = {}
        llm = data.get("llm") or {}
        current = get_config().llm
        test_cfg = LLMConfig(
            provider=(llm.get("provider") or "").strip() or current.provider,
            model=(llm.get("model") or "").strip() or current.model,
            api_key=(llm.get("api_key") or "").strip() or current.api_key,
            base_url=(llm.get("base_url") or "").strip() or current.base_url,
        )
        if not test_cfg.api_key:
            return jsonify(ok=False, error="请先填写 API Key"), 400

        from .llm.client import LLMClient

        try:
            client = LLMClient(test_cfg)
            started = time.time()
            resp = client.complete(
                system_prompt="你是连接测试助手,请只回复两个字:OK",
                user_prompt="连接测试",
                max_tokens=128,
            )
            return jsonify(
                ok=True,
                elapsed_seconds=round(time.time() - started, 2),
                reply=(resp.text or "").strip()[:100],
            )
        except Exception as e:
            logger.warning("连接测试失败: %s", e)
            return jsonify(ok=False, error=str(e)), 400

    return app


_HISTORY_RE = re.compile(r"_(\d{8}_\d{6})\.(xlsx|csv|md)$")


def _list_history(output_dir: Path) -> List[Dict[str, Any]]:
    """扫描输出目录,按批次(时间戳)分组,倒序返回。"""
    batches: Dict[str, Dict[str, Any]] = {}
    if not output_dir.is_dir():
        return []

    for p in output_dir.iterdir():
        if not p.is_file():
            continue
        m = _HISTORY_RE.search(p.name)
        if not m:
            continue
        ts = m.group(1)
        parts = p.stem.split("_")
        device = parts[1] if len(parts) >= 3 else "?"
        batch = batches.setdefault(ts, {"time": ts, "device_code": device, "files": []})
        batch["files"].append(
            {"name": p.name, "url": f"/api/download/{p.name}", "ext": m.group(2)}
        )

    items = sorted(batches.values(), key=lambda b: b["time"], reverse=True)
    for b in items:
        try:
            b["time_display"] = datetime.strptime(b["time"], "%Y%m%d_%H%M%S").strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            b["time_display"] = b["time"]
    return items


def start_web_ui(host: str = "127.0.0.1", port: int = 5050, open_browser: bool = True) -> None:
    """启动 Web UI。"""
    cfg = get_config()

    # 配置日志
    logging.basicConfig(
        level=os.environ.get("AGENT_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = create_app()
    upload_dir = Path(app.config["UPLOAD_DIR"])
    output_dir = Path(app.config["OUTPUT_DIR"])

    url = f"http://{host}:{port}"
    print("=" * 60)
    print(f"  板检数据核验 Agent 已启动")
    print(f"  访问地址: {url}")
    print(f"  设备目录: {Path(__file__).parent / 'devices'}")
    print(f"  上传目录: {upload_dir}")
    print(f"  输出目录: {output_dir}")
    print(f"  API Key:  {'已配置 (尾号 …%s)' % cfg.llm.api_key[-4:] if cfg.llm.api_key else '未配置(可在页面上方「LLM 设置」中填写)'}")
    print("=" * 60)

    if open_browser:
        # 延迟 1 秒打开浏览器,避免阻塞
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    # 关闭 debug,关闭 reloader (PyInstaller 友好);threaded 允许后台生成任务与轮询并行
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
