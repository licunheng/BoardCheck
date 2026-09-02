// 板检数据核验工具 - 前端逻辑

let state = {
  deviceCode: null,
  rawCount: 0,
  platformCount: 0,
  rawFiles: [],
  platformFiles: [],
  result: null,
};

// === 加载设备列表 ===
async function loadDevices() {
  try {
    const r = await fetch('/api/devices');
    const data = await r.json();
    const sel = document.getElementById('device-select');
    sel.innerHTML = '<option value="">-- 请选择设备 --</option>';
    (data.devices || []).forEach((d) => {
      const opt = document.createElement('option');
      opt.value = d.code;
      opt.textContent = `${d.code} (${d.name})${d.is_4g ? ' [4G]' : ''}`;
      sel.appendChild(opt);
    });
  } catch (e) {
    console.error('加载设备列表失败', e);
  }
}

document.getElementById('device-select').addEventListener('change', (e) => {
  state.deviceCode = e.target.value;
  const info = document.getElementById('device-info');
  if (state.deviceCode) {
    info.textContent = `已选择: ${state.deviceCode}`;
    info.className = 'badge ok';
    info.style.display = 'inline-block';
  } else {
    info.style.display = 'none';
  }
  refreshVerifyButton();
});

// === LLM 设置 ===
async function loadSettings() {
  try {
    const r = await fetch('/api/settings');
    const data = await r.json();
    const llm = data.llm || {};
    document.getElementById('set-provider').value = llm.provider || 'anthropic';
    document.getElementById('set-model').value = llm.model || '';
    document.getElementById('set-base-url').value = llm.base_url || '';
    const badge = document.getElementById('llm-status-badge');
    if (llm.api_key_set) {
      badge.textContent = `已配置 (尾号 …${llm.api_key_hint})`;
    } else {
      badge.textContent = '未配置(纯规则核验即可用)';
    }
  } catch (e) {
    console.error('加载设置失败', e);
  }
}

function collectSettings() {
  return {
    provider: document.getElementById('set-provider').value,
    model: document.getElementById('set-model').value.trim(),
    api_key: document.getElementById('set-api-key').value,
    base_url: document.getElementById('set-base-url').value.trim(),
  };
}

async function saveSettings() {
  const status = document.getElementById('settings-status');
  status.className = 'status';
  status.textContent = '保存中...';
  try {
    const r = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ llm: collectSettings() }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '保存失败');
    document.getElementById('set-api-key').value = '';
    status.className = 'status ok';
    status.textContent = '设置已保存';
    loadSettings();
  } catch (e) {
    status.className = 'status err';
    status.textContent = `${e.message}`;
  }
}

async function testSettings() {
  const status = document.getElementById('settings-status');
  status.className = 'status';
  status.textContent = '测试连接中...';
  try {
    const r = await fetch('/api/settings/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ llm: collectSettings() }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '连接失败');
    status.className = 'status ok';
    status.textContent = `连接成功 (${data.elapsed_seconds} 秒): ${data.reply}`;
  } catch (e) {
    status.className = 'status err';
    status.textContent = `${e.message}`;
  }
}

// === 存储目录设置 ===
async function loadStorage() {
  try {
    const r = await fetch('/api/storage');
    const data = await r.json();
    document.getElementById('set-upload-dir').value = data.upload_dir || '';
    document.getElementById('set-output-dir').value = data.output_dir || '';
  } catch (e) {
    console.error('加载存储设置失败', e);
  }
}

async function saveStorage() {
  const status = document.getElementById('storage-status');
  status.className = 'status';
  status.textContent = '保存中...';
  try {
    const r = await fetch('/api/storage', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        upload_dir: document.getElementById('set-upload-dir').value.trim(),
        output_dir: document.getElementById('set-output-dir').value.trim(),
      }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '保存失败');
    status.className = 'status ok';
    status.textContent = `已保存: 上传 ${data.upload_dir} / 输出 ${data.output_dir}`;
  } catch (e) {
    status.className = 'status err';
    status.textContent = `${e.message}`;
  }
}

// === 上传板检数据(多文件) ===
async function uploadRaw() {
  if (!state.deviceCode) {
    alert('请先选择设备型号');
    return;
  }
  const files = document.getElementById('raw-file').files;
  if (!files || files.length === 0) {
    alert('请选择文件');
    return;
  }
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  fd.append('device_code', state.deviceCode);
  const status = document.getElementById('raw-status');
  status.className = 'status';
  status.textContent = `上传 ${files.length} 个文件中...`;
  try {
    const r = await fetch('/api/upload/raw', { method: 'POST', body: fd });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '上传失败');
    state.rawCount = (data.raw_files || []).length;
    state.rawFiles = data.raw_files || [];
    status.className = 'status ok';
    const skipped = data.skipped || [];
    const skipText = skipped.length
      ? `,跳过 ${skipped.length} 个:${skipped.map((s) => `${s.name}(${s.reason})`).join('; ')}`
      : '';
    status.textContent = `已上传 ${data.count} 个文件,共 ${state.rawCount} 个${skipText}`;
    document.getElementById('step-raw').classList.add('completed');
    renderRawList();
    refreshVerifyButton();
  } catch (e) {
    status.className = 'status err';
    status.textContent = `${e.message}`;
    document.getElementById('step-raw').classList.add('error');
  }
}

// === 上传平台 CSV(多文件) ===
async function uploadPlatform() {
  const files = document.getElementById('platform-file').files;
  if (!files || files.length === 0) {
    alert('请选择文件');
    return;
  }
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  const status = document.getElementById('platform-status');
  status.className = 'status';
  status.textContent = `上传 ${files.length} 个文件中...`;
  try {
    const r = await fetch('/api/upload/platform', { method: 'POST', body: fd });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '上传失败');
    state.platformCount = (data.platform_files || []).length;
    state.platformFiles = data.platform_files || [];
    status.className = 'status ok';
    const skipped = data.skipped || [];
    const skipText = skipped.length
      ? `,跳过 ${skipped.length} 个:${skipped.map((s) => `${s.name}(${s.reason})`).join('; ')}`
      : '';
    status.textContent = `已上传 ${data.count} 个文件,共 ${state.platformCount} 个${skipText}`;
    document.getElementById('step-platform').classList.add('completed');
    renderPlatformList();
    refreshVerifyButton();
  } catch (e) {
    status.className = 'status err';
    status.textContent = `${e.message}`;
    document.getElementById('step-platform').classList.add('error');
  }
}

// === 已上传文件列表 + 删除 ===
function renderRawList() {
  const area = document.getElementById('raw-list');
  area.innerHTML = state.rawFiles.map((f, i) => `
    <div class="file-row">
      <span class="fname">${escapeHtml(f.filename || f.name || '文件' + (i + 1))}</span>
      <button onclick="deleteRaw(${i})">删除</button>
    </div>`).join('');
}

function renderPlatformList() {
  const area = document.getElementById('platform-list');
  area.innerHTML = state.platformFiles.map((f, i) => `
    <div class="file-row">
      <span class="fname">${escapeHtml(f.filename || f.name || '文件' + (i + 1))}</span>
      <button onclick="deletePlatform(${i})">删除</button>
    </div>`).join('');
}

async function deleteRaw(index) {
  try {
    const r = await fetch('/api/upload/raw', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '删除失败');
    state.rawFiles = data.files || [];
    state.rawCount = state.rawFiles.length;
    renderRawList();
    if (state.rawCount === 0) document.getElementById('step-raw').classList.remove('completed');
    refreshVerifyButton();
  } catch (e) {
    alert(`删除失败: ${e.message}`);
  }
}

async function deletePlatform(index) {
  try {
    const r = await fetch('/api/upload/platform', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '删除失败');
    state.platformFiles = data.files || [];
    state.platformCount = state.platformFiles.length;
    renderPlatformList();
    if (state.platformCount === 0) document.getElementById('step-platform').classList.remove('completed');
    refreshVerifyButton();
  } catch (e) {
    alert(`删除失败: ${e.message}`);
  }
}

// === LLM 复核开关 ===
async function loadReviewSettings() {
  try {
    const r = await fetch('/api/review/settings');
    const data = await r.json();
    const rev = data.review || {};
    document.getElementById('rev-master').checked = !!rev.llm_review;
    document.getElementById('rev-independent').checked = !!rev.independent_extraction;
    document.getElementById('rev-comparison').checked = !!rev.comparison_review;
    document.getElementById('rev-sub').style.display = rev.llm_review ? 'block' : 'none';
  } catch (e) {
    console.error('加载复核设置失败', e);
  }
}

function collectReview() {
  return {
    llm_review: document.getElementById('rev-master').checked,
    independent_extraction: document.getElementById('rev-independent').checked,
    comparison_review: document.getElementById('rev-comparison').checked,
  };
}

document.getElementById('rev-master').addEventListener('change', (e) => {
  document.getElementById('rev-sub').style.display = e.target.checked ? 'block' : 'none';
});

// === 开始核验(后台任务 + 轮询进度) ===
async function startVerify() {
  const status = document.getElementById('verify-status');
  const btn = document.getElementById('btn-verify');
  btn.disabled = true;
  status.className = 'status';
  status.textContent = '提交核验任务...';
  try {
    const r = await fetch('/api/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collectReview()),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '核验失败');
    await pollVerifyStatus(data.job_id, status, btn);
  } catch (e) {
    status.className = 'status err';
    status.textContent = `${e.message}`;
    document.getElementById('step-verify').classList.add('error');
    btn.disabled = false;
  }
}

async function pollVerifyStatus(jobId, status, btn) {
  const start = Date.now();
  let done = false;
  while (!done) {
    const elapsed = Math.round((Date.now() - start) / 1000);
    try {
      const r = await fetch(`/api/verify/status?job_id=${encodeURIComponent(jobId)}`);
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || '查询失败');
      const job = data.job;
      status.className = 'status';
      status.textContent = `${job.message || '核验中'} (${formatDuration(elapsed)})`;
      if (job.status === 'done') {
        state.result = job.result;
        status.className = 'status ok';
        status.textContent = `完成,耗时 ${formatDuration(elapsed)}`;
        document.getElementById('step-verify').classList.add('completed');
        renderResult(job.result);
        loadHistory();
        done = true;
      } else if (job.status === 'error') {
        status.className = 'status err';
        status.textContent = `${job.error || '核验失败'}`;
        document.getElementById('step-verify').classList.add('error');
        done = true;
      }
    } catch (e) {
      status.className = 'status err';
      status.textContent = `${e.message}`;
      done = true;
    }
    if (!done) {
      await new Promise((res) => setTimeout(res, 1500));
    }
  }
  btn.disabled = false;
}

// === 渲染结果 ===
function renderResult(result) {
  const area = document.getElementById('download-area');
  const summary = result.summary || {};
  const quality = result.quality || {};
  let qualityHtml = '';
  if (quality.raw_count !== undefined) {
    qualityHtml = `
      <div class="hint" style="margin-top:0.75rem;">
        数据质量: 去重 ${quality.deduped || 0} 条
        · 一一对应冲突 <strong style="color:${quality.conflicts ? 'var(--danger)' : 'var(--success)'};">${quality.conflicts || 0}</strong> 处
        · ICCID 长度异常 ${quality.truncated_iccid || 0} 条
      </div>`;
  }
  area.innerHTML = `
    <div class="result-summary">
      <div class="result-card">
        <div class="num">${summary.total || 0}</div>
        <div class="label">总设备数</div>
      </div>
      <div class="result-card success">
        <div class="num">${summary.match || 0}</div>
        <div class="label">完全一致</div>
      </div>
      <div class="result-card danger">
        <div class="num">${summary.mismatch || 0}</div>
        <div class="label">字段不一致</div>
      </div>
      <div class="result-card warning">
        <div class="num">${summary.raw_only || 0}</div>
        <div class="label">仅原始有</div>
      </div>
      <div class="result-card warning">
        <div class="num">${summary.platform_only || 0}</div>
        <div class="label">仅平台有</div>
      </div>
    </div>
    ${qualityHtml}
    <ul class="download-list">
      <li><span class="name">详细对比表.xlsx</span><a href="/api/download/${encodeURIComponent(basename(result.xlsx_path))}" target="_blank">下载</a></li>
      <li><span class="name">重传数据.csv</span><a href="/api/download/${encodeURIComponent(basename(result.csv_path))}" target="_blank">下载</a></li>
      <li><span class="name">AI 分析报告.md</span><a href="/api/download/${encodeURIComponent(basename(result.md_path))}" target="_blank">下载</a></li>
    </ul>
  `;
}

// === 历史记录(刷新不丢失) ===
async function loadHistory() {
  const area = document.getElementById('history-area');
  try {
    const r = await fetch('/api/history');
    const data = await r.json();
    const items = data.history || [];
    if (items.length === 0) {
      area.innerHTML = '<div class="empty">暂无历史记录</div>';
      return;
    }
    let html = '';
    for (const it of items) {
      const files = (it.files || []).map((f) => {
        const icon = f.ext === 'xlsx' ? '[XLSX]' : f.ext === 'csv' ? '[CSV]' : '[MD]';
        return `<a href="${f.url}" target="_blank">${icon} ${f.name}</a>`;
      }).join('');
      html += `
        <div class="history-item">
          <div class="meta">${it.time_display} · 设备 ${it.device_code}</div>
          <div class="files">${files}</div>
        </div>`;
    }
    area.innerHTML = html;
  } catch (e) {
    console.error('加载历史失败', e);
    area.innerHTML = '<div class="empty">加载历史失败</div>';
  }
}

// === 设备配置生成(后台任务 + 轮询进度) ===
async function generateDevice() {
  const status = document.getElementById('gen-status');
  const btn = document.getElementById('btn-generate');
  const code = document.getElementById('gen-code').value.trim();
  const desc = document.getElementById('gen-desc').value.trim();
  const log = document.getElementById('gen-log').value;
  const csv = document.getElementById('gen-csv').value;
  const is4g = document.getElementById('gen-is4g').value;
  const overwrite = document.getElementById('gen-overwrite').checked;

  if (!code) { alert('请填写设备代号'); return; }
  if (!desc) { alert('请填写板检规则描述'); return; }

  btn.disabled = true;
  status.className = 'status';
  status.textContent = '提交任务...';
  document.getElementById('gen-result').innerHTML = '';
  try {
    const r = await fetch('/api/devices/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        device_code: code,
        description: desc,
        sample_log: log,
        sample_csv: csv,
        is_4g_hint: is4g === '' ? null : (is4g === 'true'),
        overwrite,
      }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '生成失败');
    await pollGenerateStatus(data.job_id, status, btn);
  } catch (e) {
    status.className = 'status err';
    status.textContent = `${e.message}`;
    btn.disabled = false;
  }
}

async function pollGenerateStatus(jobId, status, btn) {
  const box = document.getElementById('gen-result');
  const start = Date.now();
  let done = false;
  while (!done) {
    let elapsed = Math.round((Date.now() - start) / 1000);
    try {
      const r = await fetch(`/api/devices/generate/status?job_id=${encodeURIComponent(jobId)}`);
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || '查询失败');
      const job = data.job;
      status.className = 'status';
      status.textContent = `${job.message || '生成中'} (${formatDuration(elapsed)})`;
      if (job.status === 'done') {
        status.className = 'status ok';
        status.textContent = `生成完成 (${formatDuration(elapsed)})`;
        renderGenResult(job.result);
        loadDevices();
        done = true;
      } else if (job.status === 'error') {
        status.className = 'status err';
        status.textContent = `生成失败`;
        box.innerHTML = `<div class="hint" style="color:var(--danger);">${escapeHtml(job.error || '未知错误')}</div>`;
        done = true;
      }
    } catch (e) {
      status.className = 'status err';
      status.textContent = `${e.message}`;
      done = true;
    }
    if (!done) {
      await new Promise((res) => setTimeout(res, 2000));
    }
  }
  btn.disabled = false;
}

function renderGenResult(res) {
  const box = document.getElementById('gen-result');
  const p = res.preview || {};
  const cov = p.field_coverage || {};
  let covHtml = '';
  for (const [k, v] of Object.entries(cov)) {
    covHtml += `<li>${k}: ${v}%</li>`;
  }
  const issues = (p.issues || []).map((s) => `<li style="color:var(--danger);">${s}</li>`).join('');
  box.innerHTML = `
    <div class="hint">
      <strong>设备 ${res.device_code}</strong> (${res.display_name}, ${res.is_4g ? '4G' : '非4G'})
      · 共调用大模型 <strong>${res.calls}</strong> 次
      · 已写入 <code>${res.rules_dir}</code>
    </div>
    <div class="result-summary">
      <div class="result-card"><div class="num">${p.records ?? '-'}</div><div class="label">样例抽取记录数</div></div>
      <div class="result-card"><div class="num">${p.compiled ? '通过' : '失败'}</div><div class="label">正则可编译</div></div>
      <div class="result-card ${res.fixed ? 'success' : ''}"><div class="num">${res.fixed ? '是' : '否'}</div><div class="label">已自动修正</div></div>
    </div>
    ${covHtml ? `<div style="font-size:0.85rem;"><strong>样例字段抽全率:</strong><ul>${covHtml}</ul></div>` : ''}
    ${issues ? `<div style="font-size:0.85rem;"><strong>待人工复核:</strong><ul>${issues}</ul></div>` : ''}
    <div style="font-size:0.8rem;color:var(--text-muted);margin-top:0.5rem;">
      已可在「选择设备型号」下拉中看到该设备;建议先做一次「规则自检」或用真实日志跑一遍核验验证效果。
    </div>
  `;
}

// === 工具函数 ===
function formatBytes(b) {
  if (b < 1024) return b + ' B';
  if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
  return (b / 1024 / 1024).toFixed(2) + ' MB';
}

function basename(p) {
  if (!p) return '';
  return String(p).split(/[/\\]/).pop();
}

function formatDuration(s) {
  s = Math.max(0, Math.round(s || 0));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m} 分 ${sec} 秒`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function refreshVerifyButton() {
  const btn = document.getElementById('btn-verify');
  btn.disabled = !(state.deviceCode && state.rawCount > 0 && state.platformCount > 0);
}

// 初始化
loadDevices();
loadSettings();
loadStorage();
loadReviewSettings();
loadHistory();
