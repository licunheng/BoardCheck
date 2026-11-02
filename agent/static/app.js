// 板检数据核验工具 - 前端逻辑 (Dark UI / 多页 SPA)

let state = {
  deviceCode: null,
  rawCount: 0,
  platformCount: 0,
  rawFiles: [],
  platformFiles: [],
  result: null,
  llmConfigured: false,
};

const ROUTE_TITLES = {
  home: '总览',
  verify: '数据核验',
  history: '历史记录',
  devices: '设备管理',
  generate: '新增设备',
  settings: '设置',
};

// === 路由 ===
function nav(route) {
  if (!ROUTE_TITLES[route]) route = 'home';
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const page = document.getElementById('page-' + route);
  page.classList.add('active');
  page.style.animation = 'none';
  void page.offsetWidth;
  page.style.animation = '';
  document.querySelectorAll('.nav').forEach(n => n.classList.remove('active'));
  document.querySelector(`.nav[data-route="${route}"]`)?.classList.add('active');
  document.getElementById('bc').textContent = ROUTE_TITLES[route];
  location.hash = route;
  if (route === 'home') loadHome();
  if (route === 'verify' && currentStep === 4) animateResults();
  if (route === 'history') loadHistory();
  if (route === 'devices') loadDevicesGrid();
  if (route === 'settings') { loadSettings(); loadStorage(); loadReviewSettings(); }
}
window.addEventListener('hashchange', () => nav(location.hash.slice(1) || 'home'));

// === Toast ===
function toast(msg, kind = 'ok', ms = 3000) {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.innerHTML = `<div class="icon">${kind === 'ok' ? '✓' : '!'}</div><div class="msg">${escapeHtml(msg)}</div>`;
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => {
    el.style.transition = 'all .3s';
    el.style.opacity = '0';
    el.style.transform = 'translateX(20px)';
    setTimeout(() => el.remove(), 300);
  }, ms);
}

// === 工具函数 ===
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}
function basename(p) {
  if (!p) return '';
  return String(p).split(/[/\\]/).pop();
}
function formatBytes(b) {
  if (b < 1024) return b + ' B';
  if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
  return (b / 1024 / 1024).toFixed(2) + ' MB';
}
function formatDuration(s) {
  s = Math.max(0, Math.round(s || 0));
  if (s < 60) return s + ' 秒';
  const m = Math.floor(s / 60);
  return m + ' 分 ' + (s % 60) + ' 秒';
}

// === 首页 ===
async function loadHome() {
  // stats
  try {
    const r = await fetch('/api/stats/summary');
    const data = await r.json();
    setCount('stat-devices', data.device_count || 0);
    setCount('stat-monthly', data.monthly_batches || 0);
    setCount('stat-llm', data.llm_review_batches || 0);
  } catch (e) {
    console.error('stats failed', e);
  }
  // 状态药丸
  try {
    const r = await fetch('/api/settings');
    const data = await r.json();
    const llm = data.llm || {};
    state.llmConfigured = !!llm.api_key_set;
    updateStatusPill();
  } catch (e) {}
  // 最近活动:复用历史列表前 5 条
  try {
    const r = await fetch('/api/history');
    const data = await r.json();
    const items = (data.history || []).slice(0, 5);
    const area = document.getElementById('home-activity');
    if (items.length === 0) {
      area.innerHTML = '<div class="empty">暂无活动记录 · 完成一次核验后会显示在这里</div>';
      return;
    }
    area.innerHTML = items.map(it => {
      const total = (it.summary && it.summary.total) || 0;
      const match = (it.summary && it.summary.match) || 0;
      const mismatch = (it.summary && it.summary.mismatch) || (total - match);
      const cls = mismatch === 0 ? 'ok' : (mismatch > total / 4 ? 'err' : 'warn');
      const sym = cls === 'ok' ? '✓' : (cls === 'err' ? '×' : '⚠');
      const summary = it.summary ? `<span class="muted">· 总 ${total} · 一致 ${match} · 不一致 ${mismatch}</span>` : '';
      return `
        <div class="act">
          <div class="act-mark ${cls}">${sym}</div>
          <div class="act-info">
            <div class="act-title">${escapeHtml(it.device_code)} 批次核验 <span class="muted">${summary}</span></div>
            <div class="act-sub">${escapeHtml(it.time_display)}</div>
          </div>
          <div class="act-time">${escapeHtml(it.device_code)}</div>
        </div>`;
    }).join('');
  } catch (e) {
    document.getElementById('home-activity').innerHTML = '<div class="empty">加载失败</div>';
  }
}

function setCount(id, target) {
  const el = document.getElementById(id);
  if (!el) return;
  const dur = 800;
  const start = performance.now();
  const tick = now => {
    const t = Math.min(1, (now - start) / dur);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = Math.round(target * eased);
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function updateStatusPill() {
  const pill = document.getElementById('status-pill');
  if (state.llmConfigured) {
    pill.className = 'status-pill';
    pill.innerHTML = '<span class="dot"></span>本地运行 · LLM 已配置';
  } else {
    pill.className = 'status-pill warn';
    pill.innerHTML = '<span class="dot"></span>本地运行 · LLM 未配置';
  }
}

// === 设备列表 ===
async function loadDevices() {
  try {
    const r = await fetch('/api/devices');
    const data = await r.json();
    const sel = document.getElementById('v-device');
    sel.innerHTML = '<option value="">-- 请选择设备 --</option>';
    (data.devices || []).forEach(d => {
      const opt = document.createElement('option');
      opt.value = d.code;
      const tag = d.is_4g ? ' [4G]' : '';
      opt.textContent = `${d.code} (${d.name})${tag}`;
      sel.appendChild(opt);
    });
    return data.devices || [];
  } catch (e) {
    console.error('加载设备列表失败', e);
    return [];
  }
}

document.getElementById('v-device').addEventListener('change', e => {
  state.deviceCode = e.target.value;
  document.getElementById('btn-gostep1').disabled = !state.deviceCode;
});

// === 设备管理卡 ===
async function loadDevicesGrid() {
  try {
    const r = await fetch('/api/devices');
    const data = await r.json();
    const grid = document.getElementById('device-grid');
    const items = data.devices || [];
    if (items.length === 0) {
      grid.innerHTML = '<div class="empty">还没有配置任何设备</div>';
      return;
    }
    grid.innerHTML = items.map(d => {
      const tag = d.is_4g ? '' : 'nb';
      const tagText = d.is_4g ? '4G' : 'NB';
      return `
        <div class="device-card">
          <div class="device-head">
            <div class="device-code">${escapeHtml(d.code)}</div>
            <div class="device-tag ${tag}">${tagText}</div>
          </div>
          <div class="device-name">${escapeHtml(d.name)}${d.version ? ' · v' + escapeHtml(d.version) : ''}</div>
          <div class="device-stats">
            <div><div class="v">${escapeHtml(d.version || '-')}</div><div class="l">版本</div></div>
            <div><div class="v">${d.is_4g ? '是' : '否'}</div><div class="l">4G</div></div>
            <div><div class="v">●</div><div class="l">就绪</div></div>
          </div>
        </div>`;
    }).join('');
  } catch (e) {
    document.getElementById('device-grid').innerHTML = '<div class="empty">加载失败</div>';
  }
}

// === LLM 设置 ===
async function loadSettings() {
  try {
    const r = await fetch('/api/settings');
    const data = await r.json();
    const llm = data.llm || {};
    document.getElementById('set-provider').value = llm.provider || 'anthropic';
    document.getElementById('set-model').value = llm.model || '';
    document.getElementById('set-base-url').value = llm.base_url || '';
    const badge = document.getElementById('llm-badge');
    const hint = document.getElementById('llm-hint');
    state.llmConfigured = !!llm.api_key_set;
    updateStatusPill();
    if (llm.api_key_set) {
      badge.className = 'tag ok';
      badge.textContent = '已配置';
      hint.textContent = '尾号 …' + (llm.api_key_hint || '');
    } else {
      badge.className = 'tag warn';
      badge.textContent = '未配置';
      hint.textContent = '(纯规则核验即可用)';
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
  status.textContent = '保存中…';
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
    toast('设置已保存', 'ok');
    loadSettings();
  } catch (e) {
    status.className = 'status err';
    status.textContent = e.message;
    toast('保存失败: ' + e.message, 'err');
  }
}

async function testSettings() {
  const status = document.getElementById('settings-status');
  status.className = 'status';
  status.textContent = '测试连接中…';
  try {
    const r = await fetch('/api/settings/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ llm: collectSettings() }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '连接失败');
    status.className = 'status ok';
    status.textContent = `连接成功 (${data.elapsed_seconds} 秒)`;
    toast('LLM 连接成功', 'ok');
  } catch (e) {
    status.className = 'status err';
    status.textContent = e.message;
    toast('连接失败: ' + e.message, 'err');
  }
}

// === 存储设置 ===
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
  status.textContent = '保存中…';
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
    status.textContent = '已保存';
    toast('存储目录已保存', 'ok');
  } catch (e) {
    status.className = 'status err';
    status.textContent = e.message;
    toast('保存失败: ' + e.message, 'err');
  }
}

// === 上传(多文件) ===
async function uploadRaw() {
  if (!state.deviceCode) { toast('请先选择设备型号', 'err'); return; }
  const files = document.getElementById('raw-input').files;
  if (!files || files.length === 0) { toast('请选择文件', 'err'); return; }
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  fd.append('device_code', state.deviceCode);
  try {
    const r = await fetch('/api/upload/raw', { method: 'POST', body: fd });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '上传失败');
    state.rawFiles = data.raw_files || [];
    state.rawCount = state.rawFiles.length;
    renderRawList();
    const skip = data.skipped || [];
    if (skip.length > 0) toast(`已上传 ${state.rawCount} 个,跳过 ${skip.length} 个`, 'ok');
    else toast(`已上传 ${state.rawCount} 个文件`, 'ok');
    document.getElementById('btn-gostep2').disabled = state.rawCount === 0;
  } catch (e) {
    toast('上传失败: ' + e.message, 'err');
  }
}

async function uploadPlatform() {
  const files = document.getElementById('plat-input').files;
  if (!files || files.length === 0) { toast('请选择文件', 'err'); return; }
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  try {
    const r = await fetch('/api/upload/platform', { method: 'POST', body: fd });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '上传失败');
    state.platformFiles = data.platform_files || [];
    state.platformCount = state.platformFiles.length;
    renderPlatformList();
    const skip = data.skipped || [];
    if (skip.length > 0) toast(`已上传 ${state.platformCount} 个,跳过 ${skip.length} 个`, 'ok');
    else toast(`已上传 ${state.platformCount} 个文件`, 'ok');
    document.getElementById('btn-gostep3').disabled = state.platformCount === 0;
  } catch (e) {
    toast('上传失败: ' + e.message, 'err');
  }
}

function renderRawList() {
  const area = document.getElementById('raw-list');
  if (state.rawFiles.length === 0) { area.innerHTML = ''; return; }
  area.innerHTML = state.rawFiles.map((f, i) => {
    const ext = (f.filename || '').split('.').pop().toUpperCase().slice(0, 3) || 'FILE';
    return `
      <div class="uploaded-item">
        <div class="file-ext">${escapeHtml(ext)}</div>
        <div class="name">${escapeHtml(f.filename || '文件')}</div>
        <div class="size">${formatBytes(f.size_bytes || 0)}</div>
        <button class="rm" onclick="deleteRaw(${i})">✕</button>
      </div>`;
  }).join('');
}

function renderPlatformList() {
  const area = document.getElementById('plat-list');
  if (state.platformFiles.length === 0) { area.innerHTML = ''; return; }
  area.innerHTML = state.platformFiles.map((f, i) => {
    const ext = (f.filename || '').split('.').pop().toUpperCase().slice(0, 3) || 'FILE';
    return `
      <div class="uploaded-item">
        <div class="file-ext">${escapeHtml(ext)}</div>
        <div class="name">${escapeHtml(f.filename || '文件')}</div>
        <div class="size">${formatBytes(f.size_bytes || 0)}</div>
        <button class="rm" onclick="deletePlatform(${i})">✕</button>
      </div>`;
  }).join('');
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
    document.getElementById('btn-gostep2').disabled = state.rawCount === 0;
  } catch (e) {
    toast('删除失败: ' + e.message, 'err');
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
    document.getElementById('btn-gostep3').disabled = state.platformCount === 0;
  } catch (e) {
    toast('删除失败: ' + e.message, 'err');
  }
}

// === 复核策略 ===
async function loadReviewSettings() {
  try {
    const r = await fetch('/api/review/settings');
    const data = await r.json();
    const rev = data.review || {};
    document.getElementById('rev-master').checked = !!rev.llm_review;
    document.getElementById('rev-compare').checked = !!rev.comparison_review;
    document.getElementById('rev-indep').checked = !!rev.independent_extraction;
    document.getElementById('rev-sub').style.display = rev.llm_review ? 'block' : 'none';
    document.getElementById('rev-master-2').checked = !!rev.llm_review;
    document.getElementById('rev-compare-2').checked = !!rev.comparison_review;
    document.getElementById('rev-indep-2').checked = !!rev.independent_extraction;
    document.getElementById('rev-sub-2').style.display = rev.llm_review ? 'block' : 'none';
  } catch (e) {
    console.error('加载复核设置失败', e);
  }
}

async function saveReview() {
  const status = document.getElementById('review-status');
  status.className = 'status';
  status.textContent = '保存中…';
  try {
    const r = await fetch('/api/review/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        llm_review: document.getElementById('rev-master-2').checked,
        comparison_review: document.getElementById('rev-compare-2').checked,
        independent_extraction: document.getElementById('rev-indep-2').checked,
      }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '保存失败');
    status.className = 'status ok';
    status.textContent = '已保存';
    toast('复核策略已保存', 'ok');
    loadReviewSettings();
  } catch (e) {
    status.className = 'status err';
    status.textContent = e.message;
    toast('保存失败: ' + e.message, 'err');
  }
}

document.getElementById('rev-master').addEventListener('change', e => {
  document.getElementById('rev-sub').style.display = e.target.checked ? 'block' : 'none';
  document.getElementById('rev-master-2').checked = e.target.checked;
  document.getElementById('rev-sub-2').style.display = e.target.checked ? 'block' : 'none';
});
document.getElementById('rev-master-2').addEventListener('change', e => {
  document.getElementById('rev-sub-2').style.display = e.target.checked ? 'block' : 'none';
  document.getElementById('rev-master').checked = e.target.checked;
  document.getElementById('rev-sub').style.display = e.target.checked ? 'block' : 'none';
});

// === 步骤切换(数据核验页) ===
let currentStep = 0;
function goStep(n) {
  currentStep = n;
  document.querySelectorAll('.step').forEach((s, i) => {
    s.classList.toggle('active', i === n);
    s.classList.toggle('done', i < n);
  });
  document.querySelectorAll('.step-link').forEach((l, i) => l.classList.toggle('done', i < n));
  document.querySelectorAll('[data-panel]').forEach((p, i) => p.classList.toggle('hidden', i !== n));
}

// === 文件 input change → 自动上传 ===
document.getElementById('raw-input').addEventListener('change', () => uploadRaw());
document.getElementById('plat-input').addEventListener('change', () => uploadPlatform());

// 拖拽
['drop-raw'].forEach(id => {
  const el = document.getElementById(id);
  if (!el) return;
  ;['dragover', 'dragenter'].forEach(ev => el.addEventListener(ev, e => { e.preventDefault(); el.classList.add('over'); }));
  ;['dragleave', 'drop'].forEach(ev => el.addEventListener(ev, e => { e.preventDefault(); el.classList.remove('over'); }));
  el.addEventListener('drop', e => {
    e.preventDefault();
    const dt = e.dataTransfer;
    if (!dt || !dt.files.length) return;
    document.getElementById('raw-input').files = dt.files;
    uploadRaw();
  });
});
['drop-plat'].forEach(id => {
  const el = document.getElementById(id);
  if (!el) return;
  ;['dragover', 'dragenter'].forEach(ev => el.addEventListener(ev, e => { e.preventDefault(); el.classList.add('over'); }));
  ;['dragleave', 'drop'].forEach(ev => el.addEventListener(ev, e => { e.preventDefault(); el.classList.remove('over'); }));
  el.addEventListener('drop', e => {
    e.preventDefault();
    const dt = e.dataTransfer;
    if (!dt || !dt.files.length) return;
    document.getElementById('plat-input').files = dt.files;
    uploadPlatform();
  });
});

// === 开始核验 ===
function collectReview() {
  return {
    llm_review: document.getElementById('rev-master').checked,
    independent_extraction: document.getElementById('rev-indep').checked,
    comparison_review: document.getElementById('rev-compare').checked,
  };
}

async function startVerify() {
  const btn = document.getElementById('btn-verify');
  btn.disabled = true;
  goStep(4);
  document.getElementById('v-progress').classList.remove('hidden');
  document.getElementById('v-result').classList.add('hidden');
  try {
    const r = await fetch('/api/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collectReview()),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '核验失败');
    await pollVerifyStatus(data.job_id);
  } catch (e) {
    toast('核验失败: ' + e.message, 'err');
    btn.disabled = false;
  }
}

async function pollVerifyStatus(jobId) {
  const ring = document.querySelector('.orbit-fg');
  const C = 2 * Math.PI * 92;
  const msgs = ['解析板检文件…', '解析平台 CSV…', '建立设备映射…', '逐条核验字段…', '生成报告…'];
  const start = Date.now();
  let pct = 0;
  let lastPct = -1;
  return new Promise((resolve) => {
    const tick = async () => {
      const elapsed = Math.round((Date.now() - start) / 1000);
      try {
        const r = await fetch('/api/verify/status?job_id=' + encodeURIComponent(jobId));
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || '查询失败');
        const job = data.job;
        // 进度环按 elapsed 推算(后端不返回 pct 时给视觉反馈)
        pct = Math.min(99, pct + 3 + Math.random() * 2);
        ring.style.strokeDashoffset = C * (1 - pct / 100);
        if (Math.round(pct) !== lastPct) {
          document.getElementById('v-pct').textContent = Math.round(pct);
          document.getElementById('v-elapsed').textContent = '已耗时 ' + elapsed + ' 秒';
          const i = Math.min(4, Math.floor(pct / 20));
          document.getElementById('v-msg').textContent = job.message || msgs[i];
          lastPct = Math.round(pct);
        }
        if (job.status === 'done') {
          ring.style.strokeDashoffset = 0;
          document.getElementById('v-pct').textContent = 100;
          document.getElementById('v-elapsed').textContent = '已完成';
          state.result = job.result;
          setTimeout(() => {
            document.getElementById('v-progress').classList.add('hidden');
            document.getElementById('v-result').classList.remove('hidden');
            renderResult(job.result);
            resolve();
          }, 400);
        } else if (job.status === 'error') {
          toast('核验失败: ' + (job.error || '未知错误'), 'err');
          document.getElementById('v-progress').classList.add('hidden');
          document.getElementById('btn-verify').disabled = false;
          resolve();
        } else {
          setTimeout(tick, 1200);
        }
      } catch (e) {
        toast(e.message, 'err');
        document.getElementById('v-progress').classList.add('hidden');
        document.getElementById('btn-verify').disabled = false;
        resolve();
      }
    };
    tick();
  });
}

function renderResult(result) {
  const summary = (result && result.summary) || {};
  const quality = (result && result.quality) || {};
  const total = summary.total || 0;
  const match = summary.match || 0;
  const mismatch = summary.mismatch || 0;
  const rawOnly = summary.raw_only || 0;
  const platOnly = summary.platform_only || 0;

  setCount('r-total', total);
  setCount('r-match', match);
  setCount('r-mismatch', mismatch);
  setCount('r-raw-only', rawOnly);
  setCount('r-plat-only', platOnly);

  const elapsed = (result && result.elapsed_seconds) || 0;
  document.getElementById('v-result-sub').textContent =
    '耗时 ' + formatDuration(elapsed) + ' · 设备 ' + (result.device_code || '');

  if (quality.deduped !== undefined) {
    const q = document.getElementById('v-quality');
    q.classList.remove('hidden');
    q.innerHTML = `数据质量 · 去重 <strong>${quality.deduped || 0}</strong> 条 · ICCID 长度异常 <strong>${quality.truncated_iccid || 0}</strong> 条`;
  }

  const dl = document.getElementById('v-downloads');
  const items = [];
  if (result.xlsx_path) items.push({ name: '详细对比表.xlsx', path: result.xlsx_path, ext: 'XLSX' });
  if (result.csv_path) items.push({ name: '重传数据.csv', path: result.csv_path, ext: 'CSV' });
  if (result.md_path) items.push({ name: 'AI 分析报告.md', path: result.md_path, ext: 'MD' });
  dl.innerHTML = items.map(it => `
    <div class="download-row">
      <div class="download-icon">${it.ext}</div>
      <div class="name">${escapeHtml(it.name)}</div>
      <div class="meta">${escapeHtml(basename(it.path))}</div>
      <a href="/api/download/${encodeURIComponent(basename(it.path))}" target="_blank" style="color:var(--primary-2);font-size:13px;font-weight:500;text-decoration:none">下载 →</a>
    </div>`).join('');

  toast(`核验完成 · ${match}/${total} 一致`, 'ok');
  loadHistory();
}

function animateResults() {
  // renderResult 已经做了数字计数动画
}

// === 历史记录 ===
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
    area.innerHTML = items.map(it => {
      const files = (it.files || []).map(f =>
        `<a href="${f.url}" target="_blank" style="color:var(--primary-2);text-decoration:none;margin-right:12px;font-size:12px">[${f.ext.toUpperCase()}] ${escapeHtml(f.name)}</a>`
      ).join('');
      return `
        <div class="act">
          <div class="act-mark ok">✓</div>
          <div class="act-info">
            <div class="act-title">设备 ${escapeHtml(it.device_code)}</div>
            <div class="act-sub">${escapeHtml(it.time_display)}</div>
            <div class="act-sub" style="margin-top:4px">${files}</div>
          </div>
          <div class="act-time">${(it.files || []).length} 个文件</div>
        </div>`;
    }).join('');
  } catch (e) {
    area.innerHTML = '<div class="empty">加载历史失败</div>';
  }
}

// === 新增设备配置 ===
async function generateDevice() {
  const btn = document.getElementById('btn-generate');
  const status = document.getElementById('gen-status');
  const code = document.getElementById('gen-code').value.trim();
  const desc = document.getElementById('gen-desc').value.trim();
  if (!code) { toast('请填写设备代号', 'err'); return; }
  if (!desc) { toast('请填写板检规则描述', 'err'); return; }
  btn.disabled = true;
  status.className = 'status';
  status.textContent = '提交任务…';
  document.getElementById('gen-result').innerHTML = '';
  try {
    const r = await fetch('/api/devices/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        device_code: code,
        description: desc,
        sample_log: document.getElementById('gen-log').value,
        sample_csv: document.getElementById('gen-csv').value,
        is_4g_hint: document.getElementById('gen-is4g').value === '' ? null : document.getElementById('gen-is4g').value === 'true',
        overwrite: document.getElementById('gen-overwrite').checked,
      }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '生成失败');
    await pollGenerate(data.job_id, status, btn);
  } catch (e) {
    status.className = 'status err';
    status.textContent = e.message;
    toast('生成失败: ' + e.message, 'err');
    btn.disabled = false;
  }
}

async function pollGenerate(jobId, status, btn) {
  const start = Date.now();
  const tick = async () => {
    const elapsed = Math.round((Date.now() - start) / 1000);
    try {
      const r = await fetch('/api/devices/generate/status?job_id=' + encodeURIComponent(jobId));
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || '查询失败');
      const job = data.job;
      status.className = 'status';
      status.textContent = (job.message || '生成中') + ' (' + elapsed + ' 秒)';
      if (job.status === 'done') {
        status.className = 'status ok';
        status.textContent = '生成完成 (' + elapsed + ' 秒)';
        renderGenResult(job.result);
        loadDevices();
        btn.disabled = false;
      } else if (job.status === 'error') {
        status.className = 'status err';
        status.textContent = '生成失败';
        document.getElementById('gen-result').innerHTML =
          '<div class="gen-result"><div class="meta" style="color:var(--err)">' + escapeHtml(job.error || '未知错误') + '</div></div>';
        btn.disabled = false;
      } else {
        setTimeout(tick, 2000);
      }
    } catch (e) {
      status.className = 'status err';
      status.textContent = e.message;
      btn.disabled = false;
    }
  };
  tick();
}

function renderGenResult(res) {
  const box = document.getElementById('gen-result');
  const p = (res && res.preview) || {};
  const cov = p.field_coverage || {};
  const covHtml = Object.keys(cov).length
    ? '<div style="margin-top:10px;font-size:12px"><strong>样例字段抽全率:</strong><ul style="margin:6px 0 0 18px;padding:0">' +
      Object.entries(cov).map(([k, v]) => `<li>${escapeHtml(k)}: ${v}%</li>`).join('') + '</ul></div>'
    : '';
  const issues = (p.issues || []).map(s => `<li style="color:var(--err)">${escapeHtml(s)}</li>`).join('');
  box.innerHTML = `
    <div class="gen-result">
      <div class="meta"><strong>${escapeHtml(res.device_code)}</strong> (${escapeHtml(res.display_name || '')})
        · 调用大模型 <strong>${res.calls || 0}</strong> 次
        · 写入 <code>${escapeHtml(res.rules_dir || '')}</code>
      </div>
      <div style="margin-top:8px;font-size:12px;color:var(--ink-3)">
        样例抽取 <strong>${p.records ?? '-'}</strong> 条 · 正则可编译 <strong style="color:${p.compiled ? 'var(--ok)' : 'var(--err)'}">${p.compiled ? '通过' : '失败'}</strong>
        · ${res.fixed ? '<span style="color:var(--ok)">已自动修正</span>' : '未修正'}
      </div>
      ${covHtml}
      ${issues ? '<div style="margin-top:8px;font-size:12px"><strong>待人工复核:</strong><ul style="margin:6px 0 0 18px;padding:0">' + issues + '</ul></div>' : ''}
    </div>`;
  toast('设备 ' + res.device_code + ' 已生成', 'ok');
}

// === Tabs (settings) ===
function setTab(n, el) {
  el.parentElement.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  // 只切换 .card 容器,避免与 .tab 按钮的 data-tab 属性冲突
  document.querySelectorAll('#page-settings .card[data-tab]').forEach((c, i) => {
    c.classList.toggle('hidden', i !== n);
  });
}

// === 初始化 ===
window.addEventListener('DOMContentLoaded', () => {
  loadDevices().then(() => {
    // 切到 verify 页时启用按钮(如果已选)
    document.getElementById('btn-gostep1').disabled = !state.deviceCode;
  });
  loadSettings();
  loadStorage();
  loadReviewSettings();
  loadHistory();
  updateStatusPill();
  nav(location.hash.slice(1) || 'home');
});