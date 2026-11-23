/* =============================================================================
   非遗数字资产确认平台 —— 前端逻辑（原生 JS，无构建步骤）
   ---------------------------------------------------------------------------
   设计原则：
     1. 状态集中在 S 对象，视图函数只负责渲染，避免散落的 DOM 操作
     2. 所有请求走 req() 统一处理 401/403/422，错误信息直接来自后端中文提示
     3. 不信任后端返回的任何字符串直接拼进 innerHTML —— 统一 esc() 转义
        （项目里标题可能包含 < > 等字符，不转义会造成 XSS）
   ============================================================================= */
'use strict';

const API = '';
const S = {
  token: localStorage.getItem('ich_token') || '',
  user: JSON.parse(localStorage.getItem('ich_user') || 'null'),
  meta: null,
  selectedFile: null,
  page: 'upload',
  pending: [],
  ledger: [],
  ledgerFilter: 'all',
  motifs: [],
  motifFilter: 'all',
  currentAudit: null,
};

const LEVEL = { public: '公有母题', derived: '改编衍生', original: '原创作品' };
const STATUS = { draft: '草稿', pending: '待审核', approved: '已确权', rejected: '已驳回' };
const EVENT_LABEL = {
  root: '资产登记', audit: '权属审核', license: '授权流转', system_log: 'DB 自动审计',
};
const LICENSE_TYPE = {
  exclusive: '独占', non_exclusive: '非独占', display: '展示', derivative: '改编',
};

/* ---------------------------------------------------------------- 工具 */
const $ = (id) => document.getElementById(id);
const qs = (sel, root = document) => root.querySelector(sel);
const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(v) {
  if (v === null || v === undefined) return '';
  return String(v).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/* nz = number-or-zero。后端少返回某列时（如演示模式漏了 version_no），
   直接拼进模板会渲染出字面量 "undefined"。所有数值单元格统一走这里。 */
function nz(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return '—';
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1024 / 1024).toFixed(2) + ' MB';
}

function fmtMoney(v) {
  const n = Number(v || 0);
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function badgeLevel(l) {
  if (!l) return '<span class="badge gray">未判定</span>';
  return `<span class="badge ${esc(l)}">${esc(LEVEL[l] || l)}</span>`;
}
function badgeStatus(s) {
  if (!s) return '';
  return `<span class="badge ${esc(s)}">${esc(STATUS[s] || s)}</span>`;
}

function toast(msg, type = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  $('toasts').appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .3s, transform .3s';
    el.style.opacity = '0';
    el.style.transform = 'translateX(18px)';
    setTimeout(() => el.remove(), 320);
  }, type === 'bad' ? 5200 : 3200);
}

/* ---------------------------------------------------------------- 请求 */
async function req(path, opts = {}) {
  const headers = opts.headers || {};
  if (S.token) headers.Authorization = 'Bearer ' + S.token;
  if (opts.body && !(opts.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  let resp;
  try {
    resp = await fetch(API + path, { ...opts, headers });
  } catch (e) {
    throw new Error('无法连接后端服务，请确认 uvicorn 已启动');
  }
  if (resp.status === 401 && !path.includes('/auth/login')) {
    logout(true);
    throw new Error('登录已过期，请重新登录');
  }
  const ct = resp.headers.get('content-type') || '';
  const data = ct.includes('application/json') ? await resp.json() : await resp.text();

  if (!resp.ok) {
    let msg = '';
    if (typeof data === 'object' && data) {
      if (Array.isArray(data.detail)) {
        // FastAPI 422 校验错误：拼成可读文本
        msg = data.detail.map((d) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('；');
      } else {
        msg = data.detail || data.db_error || JSON.stringify(data);
      }
    } else {
      msg = String(data || resp.statusText);
    }
    const err = new Error(msg);
    err.status = resp.status;
    throw err;
  }
  return data;
}

/* ---------------------------------------------------------------- 登录 */
async function doLogin() {
  const username = $('loginUser').value.trim();
  const password = $('loginPass').value;
  const errBox = $('loginErr');
  errBox.textContent = '';
  if (!username || !password) { errBox.textContent = '请输入账号与口令'; return; }

  const btn = $('loginBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span> 登录中';
  try {
    const r = await req('/api/auth/login', { method: 'POST', body: { username, password } });
    S.token = r.access_token;
    S.user = r.user;
    localStorage.setItem('ich_token', S.token);
    localStorage.setItem('ich_user', JSON.stringify(S.user));
    enterApp();
  } catch (e) {
    errBox.textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = '登 录';
  }
}

function logout(silent = false) {
  S.token = '';
  S.user = null;
  localStorage.removeItem('ich_token');
  localStorage.removeItem('ich_user');
  $('app').classList.remove('on');
  $('loginScreen').style.display = 'grid';
  if (!silent) toast('已退出登录');
}

function enterApp() {
  $('loginScreen').style.display = 'none';
  $('app').classList.add('on');
  $('uName').textContent = S.user.display_name;
  $('uRole').textContent = { heritor: '传承人', expert: '评审专家', admin: '监管管理员' }[S.user.role] || S.user.role;
  $('uAvatar').textContent = S.user.display_name.slice(0, 1);

  // 按角色控制可见页签（前端只是体验层，真正的权限由后端 + 数据库 GRANT 兜底）
  const isExpert = ['expert', 'admin'].includes(S.user.role);
  $('tabWorkbench').style.display = isExpert ? '' : 'none';
  $('tabLedger').style.display = isExpert ? '' : 'none';
  $('tabMine').style.display = S.user.role === 'heritor' ? '' : 'none';

  const first = S.user.role === 'heritor' ? 'upload' : 'workbench';
  switchPage(isExpert && S.user.role !== 'heritor' ? first : 'upload');
  loadMeta();
}

function switchPage(page) {
  S.page = page;
  qsa('#tabs button').forEach((b) => b.classList.toggle('active', b.dataset.page === page));
  qsa('.page').forEach((p) => p.classList.toggle('on', p.id === 'page-' + page));
  if (page === 'motifs') loadMotifs();
  if (page === 'mine') loadMine();
  if (page === 'workbench') loadPending();
  if (page === 'ledger') loadLedger();
}

async function loadMeta() {
  try {
    S.meta = await req('/api/meta');
    const be = S.meta.ai_backend;
    if (!be.is_deep_model) {
      console.info('[AI] 当前使用轻量后端:', be.backend, '（未安装 torch 时的降级方案）');
    }
    applyDemoBanner(S.meta);
  } catch (e) {
    console.warn('meta 加载失败', e.message);
  }
}

/**
 * 演示模式横幅：把「这不是真实数据库」摆在最显眼的位置。
 * WHY 要这么显眼：演示模式下接口返回的数据、相似度、审计日志全都是真的算法
 * 跑出来的，只有"存储与约束"是模拟的 —— 如果不在界面上讲清楚，
 * 很容易被误认为「数据库触发器、GRANT、HNSW 都已经验证过了」。
 */
function applyDemoBanner(meta) {
  const bar = $('demoBanner');
  if (!bar) return;
  if (!meta || !meta.demo_mode) { bar.hidden = true; return; }
  if (meta.demo_note) $('demoBannerText').textContent = meta.demo_note;
  bar.hidden = false;
  const btn = $('demoBannerClose');
  if (btn) btn.onclick = () => { bar.hidden = true; };
}

/* ---------------------------------------------------------------- 上传 */
function initUpload() {
  const dz = $('dropzone');
  const input = $('fileInput');

  dz.addEventListener('click', () => input.click());
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
  dz.addEventListener('drop', (e) => {
    e.preventDefault();
    dz.classList.remove('drag');
    if (e.dataTransfer.files.length) pickFile(e.dataTransfer.files[0]);
  });
  input.addEventListener('change', () => {
    if (input.files.length) pickFile(input.files[0]);
  });

  $('uploadBtn').addEventListener('click', uploadAndAnalyze);
}

function pickFile(file) {
  if (!file.type.startsWith('image/')) {
    toast('请选择图片文件', 'bad');
    return;
  }
  S.selectedFile = file;
  const url = URL.createObjectURL(file);
  $('previewImg').src = url;
  $('previewImg').onload = () => {
    $('fmDim').textContent = `${$('previewImg').naturalWidth} × ${$('previewImg').naturalHeight}`;
    URL.revokeObjectURL(url);
  };
  $('fmName').textContent = file.name;
  $('fmSize').textContent = fmtSize(file.size);
  $('previewWrap').classList.add('on');
  if (!$('upTitle').value) {
    $('upTitle').value = file.name.replace(/\.[^.]+$/, '');
  }
}

async function uploadAndAnalyze() {
  const file = S.selectedFile;
  const title = $('upTitle').value.trim();
  if (!file) { toast('请先选择要上传的图片', 'bad'); return; }
  if (!title) { toast('请填写作品名称', 'bad'); return; }

  const btn = $('uploadBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span> 上传中…';
  $('analyzeBox').innerHTML = '<div class="empty"><div class="ico">◍</div>正在提取特征并检索母题库…</div>';

  try {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('title', title);
    fd.append('description', $('upDesc').value.trim());
    fd.append('auto_analyze', 'false');

    const up = await req('/api/asset/upload', { method: 'POST', body: fd });

    if (up.duplicate_of) {
      toast(`该文件已登记过（资产 #${up.duplicate_of}），未重复创建`, 'bad');
      $('analyzeBox').innerHTML = `
        <div class="warn-box">
          <b>命中文件查重</b><br>
          SHA-256 摘要与已有资产 #${up.duplicate_of} 完全一致，系统按幂等规则直接返回原记录，
          避免同一图像被重复确权引发权属争议。
        </div>
        ${renderAssetSummary(up.asset)}`;
      return;
    }

    btn.innerHTML = '<span class="spin"></span> AI 分析中…';
    const assetId = up.asset.asset_id;
    const an = await req(`/api/asset/${assetId}/analyze`, { method: 'POST' });

    $('analyzeBox').innerHTML = renderAnalyzeReport(an, up.asset);
    toast(`资产 #${assetId} 已提交，AI 建议：${LEVEL[an.suggested_level]}`, 'ok');

    // 重置上传区
    S.selectedFile = null;
    $('fileInput').value = '';
    $('previewWrap').classList.remove('on');
    $('upTitle').value = '';
    $('upDesc').value = '';
  } catch (e) {
    $('analyzeBox').innerHTML = `<div class="err">${esc(e.message)}</div>`;
    toast(e.message, 'bad');
  } finally {
    btn.disabled = false;
    btn.textContent = '上传并触发 AI 分析';
  }
}

function renderAssetSummary(a) {
  return `
    <dl class="kv">
      <dt>资产编号</dt><dd>#${a.asset_id}</dd>
      <dt>作品名称</dt><dd style="font-family:inherit">${esc(a.title)}</dd>
      <dt>文件摘要</dt><dd>${esc(a.file_hash.slice(0, 32))}…</dd>
      <dt>当前状态</dt><dd>${badgeStatus(a.audit_status)}</dd>
    </dl>`;
}

function renderAnalyzeReport(an, asset) {
  const level = an.suggested_level;
  const conf = (an.confidence * 100).toFixed(1);
  const bars = an.top_k.map((h) => {
    const pct = Math.max(2, h.similarity * 100);
    const cls = h.similarity >= 0.92 ? 'hi' : h.similarity >= 0.8 ? 'mid' : 'lo';
    return `
      <div class="simb">
        <div>
          <div class="lab">
            <b>${esc(h.name)} <span style="color:var(--ink-3);font-weight:400">${esc(h.code)}</span></b>
            <span>${h.similarity.toFixed(4)}</span>
          </div>
          <div class="bar"><i class="${cls}" style="width:${pct}%"></i></div>
        </div>
      </div>`;
  }).join('') || '<div style="font-size:12px;color:var(--ink-3)">母题库暂无可用向量</div>';

  const dupWarn = an.flagged_as === 'duplicate'
    ? `<div class="warn-box"><b>⚠ 疑似抄袭他人作品</b><br>${esc(an.reason)}</div>` : '';

  const similarAssets = (an.similar_assets || []).length
    ? `<div style="margin-top:14px">
         <div style="font-size:12px;font-weight:600;margin-bottom:7px">与现有资产比对</div>
         ${an.similar_assets.map((h) => `
           <div class="simb" style="margin-bottom:7px">
             <div>
               <div class="lab">
                 <b>#${h.id} ${esc(h.name)}</b>
                 <span>${h.similarity.toFixed(4)}</span>
               </div>
               <div class="bar"><i class="${h.similarity >= 0.95 ? 'hi' : 'lo'}" style="width:${Math.max(2, h.similarity * 100)}%"></i></div>
             </div>
           </div>`).join('')}
       </div>` : '';

  const planHtml = an.plan ? `
    <details style="margin-top:13px">
      <summary>查看 HNSW 索引执行计划（证明走的是向量索引）</summary>
      <pre class="json">${esc(an.plan)}</pre>
    </details>` : '';

  return `
    <div class="ai-panel">
      <div class="hd">
        <span class="tag">AI 建议</span>
        ${badgeLevel(level)}
        <span style="font-size:11px;color:var(--ink-3);margin-left:auto">置信度 ${conf}%</span>
      </div>
      <div class="bd">
        ${dupWarn}
        <div class="reason">${esc(an.reason)}</div>
        <div style="margin-top:15px">
          <div style="font-size:12px;font-weight:600;margin-bottom:8px">
            相似公有母题 Top-${an.top_k.length}
          </div>
          <div class="simbars">${bars}</div>
        </div>
        ${similarAssets}
        <dl class="kv">
          <dt>特征模型</dt><dd>${esc(an.model.name)}@${esc(an.model.version)}</dd>
          <dt>向量维度</dt><dd>${an.model.dim} 维（L2 归一化）</dd>
          <dt>命中规则</dt><dd>${esc(an.rule)}</dd>
          <dt>审核流水</dt><dd>#${an.audit_id}（状态 pending）</dd>
        </dl>
        ${planHtml}
      </div>
    </div>
    <div class="ok-box">
      <b>下一步</b>：确权结论由专家终审决定，AI 建议不会直接写入权属字段
      （数据库层已通过列级 GRANT 禁止传承人角色修改 <span class="code-inline">right_level</span>）。
    </div>
    ${renderAssetSummary(asset)}`;
}

/* ---------------------------------------------------------------- 母题库 */
/**
 * 母题库是全平台的"比对基准"——它决定了每件作品归入哪一档，
 * 所以这个页面要回答三件事：
 *   ① 库里到底有什么（纹样长什么样、来自哪里、什么年代）
 *   ② 它们的技术形态（向量维度、特征模型、是否已回填向量）
 *   ③ 它们怎么和真实作品发生关联（血缘依据 asset.motif_id）
 */
async function loadMotifs() {
  const grid = $('motifGrid');
  grid.innerHTML = '<div class="empty"><div class="ico">◌</div>加载中…</div>';
  try {
    const motifs = await req('/api/motifs');
    S.motifs = motifs;
    $('motifCount').textContent = `共 ${motifs.length} 条母题`;
    renderMotifGrid();
    loadMotifLinks();
  } catch (e) {
    grid.innerHTML = `<div class="empty"><div class="ico">⚠</div>${esc(e.message)}</div>`;
  }
}

function renderMotifGrid() {
  const grid = $('motifGrid');
  const f = S.motifFilter;
  const rows = f === 'referenced' ? S.motifs.filter((m) => nz(m.asset_refs) > 0)
             : f === 'unreferenced' ? S.motifs.filter((m) => !nz(m.asset_refs))
             : S.motifs;

  if (!rows.length) {
    grid.innerHTML = '<div class="empty"><div class="ico">◌</div>没有符合条件的母题</div>';
    return;
  }

  grid.innerHTML = rows.map((m) => {
    const img = m.reference_img ? `/data/${esc(m.reference_img)}` : '';
    const kws = (m.keywords || []).map((k) => `<span class="chip">${esc(k)}</span>`).join('');
    const refs = nz(m.asset_refs);
    return `
    <div class="motif-card">
      <div class="motif-thumb">
        ${img ? `<img src="${img}" alt="${esc(m.name)}" loading="lazy"
                    onerror="this.parentNode.innerHTML='<div class=\\'no-img\\'>参考图缺失</div>'">`
              : '<div class="no-img">无参考图</div>'}
        <span class="code">${esc(m.motif_code)}</span>
        <span class="vec-flag ${m.has_vector ? '' : 'off'}">${m.has_vector ? '向量已就绪' : '无向量'}</span>
      </div>
      <div class="motif-body">
        <h3>${esc(m.name)}</h3>
        <div class="meta">
          ${m.category ? `<span class="chip indigo">${esc(m.category)}</span>` : ''}
          ${m.ethnic ? `<span class="chip gold">${esc(m.ethnic)}</span>` : ''}
          ${m.region ? `<span class="chip">${esc(m.region)}</span>` : ''}
        </div>
        <div class="desc">${esc(m.description || '—')}</div>
        ${kws ? `<div class="meta">${kws}</div>` : ''}
        <dl class="motif-kv">
          <dt>年代</dt><dd>${esc(m.era || '—')}</dd>
          <dt>特征模型</dt><dd>${esc(m.vector_model || '尚未回填')}</dd>
          <dt>向量维度</dt><dd>${m.vector_dim ? `${m.vector_dim} 维` : '—'}</dd>
          <dt>被引用</dt><dd><b>${refs}</b> 件已确权作品以它为血缘依据</dd>
        </dl>
      </div>
    </div>`;
  }).join('');
}

/**
 * 母题 × 作品 的关联表。
 *
 * 权限差异：/api/ledger 限 expert+admin，传承人拿不到。
 *   专家/管理员 → 用台账，能看到全平台的关联
 *   传承人     → 用 /api/asset/mine，只看自己名下的作品
 * 两条路径返回的字段名已在后端对齐（motif_code / motif_name / top1_similarity），
 * 所以下面的渲染逻辑可以完全共用，不需要分支。
 */
async function loadMotifLinks() {
  const body = $('motifLinkBody');
  if (!body) return;
  const isPriv = ['expert', 'admin'].includes(S.user && S.user.role);

  let rows = [];
  try {
    if (isPriv) {
      const led = await req('/api/ledger');
      rows = led.rows.filter((r) => r.motif_name);
    } else {
      const mine = await req('/api/asset/mine');
      // 传承人的列表接口已带 motif_name / motif_code，按母题血缘过滤即可
      rows = mine
        .filter((a) => a.motif_name)
        .map((a) => ({
          ...a,
          owner_name: S.user.display_name,
          // list_my_assets 不返回 top1_similarity —— 该列对传承人留空（显示"—"）
          top1_similarity: null,
          ai_expert_agree: a.ai_suggest_level && a.right_level
            ? a.ai_suggest_level === a.right_level : null,
        }));
    }
  } catch (e) {
    body.innerHTML = `<tr><td colspan="7"><div class="err">${esc(e.message)}</div></td></tr>`;
    return;
  }

  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7"><div class="empty"><div class="ico">◌</div>还没有作品引用母题（作品被判为公有母题或改编衍生时才会记录血缘依据）</div></td></tr>';
    return;
  }

  body.innerHTML = rows.map((r) => {
    const sim = r.top1_similarity != null ? Number(r.top1_similarity) : null;
    const agree = r.ai_expert_agree;
    const cls = sim == null ? '' : sim >= 0.92 ? 'hi' : sim >= 0.8 ? 'mid' : 'lo';
    return `
    <tr>
      <td><b>${esc(r.motif_name)}</b>
          <span style="font-family:var(--mono);font-size:11px;color:var(--ink-3);margin-left:5px">${esc(r.motif_code || '')}</span></td>
      <td>${esc(r.title)}<span style="font-size:11px;color:var(--ink-3);margin-left:6px">#${nz(r.asset_id)}</span></td>
      <td style="font-size:12px">${esc(r.owner_name || '—')}</td>
      <td>${badgeLevel(r.right_level)}</td>
      <td style="min-width:100px">
        ${sim != null
          ? `<div class="bar"><i class="${cls}" style="width:${Math.max(3, sim * 100)}%"></i></div>
             <span style="font-size:11px;font-family:var(--mono);color:var(--ink-3)">${sim.toFixed(4)}</span>`
          : '<span style="font-size:12px;color:var(--ink-3)">—</span>'}
      </td>
      <td>${agree === true ? '<span class="badge approved">一致</span>'
            : agree === false ? '<span class="badge rejected">分歧</span>'
            : '<span class="badge gray">—</span>'}</td>
      <td style="text-align:right">
        <button class="btn" style="padding:4px 10px;font-size:12px" data-trace="${nz(r.asset_id)}">溯源</button>
      </td>
    </tr>`;
  }).join('');
}

/* ---------------------------------------------------------------- 我的作品 */
async function loadMine() {
  const body = $('mineBody');
  body.innerHTML = '<tr><td colspan="8"><div class="empty">加载中…</div></td></tr>';
  try {
    const rows = await req('/api/asset/mine');
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="8"><div class="empty"><div class="ico">◌</div>还没有作品，先去「作品上传」提交一件</div></td></tr>';
      return;
    }
    body.innerHTML = rows.map((a) => `
      <tr>
        <td class="num">${a.asset_id}</td>
        <td><b>${esc(a.title)}</b></td>
        <td>${badgeLevel(a.ai_suggest_level)}${a.ai_confidence != null
          ? `<span style="font-size:11px;color:var(--ink-3);margin-left:5px">${(a.ai_confidence * 100).toFixed(0)}%</span>` : ''}</td>
        <td>${badgeLevel(a.right_level)}</td>
        <td>${badgeStatus(a.audit_status)}</td>
        <td class="num">v${nz(a.version_no, 0)}</td>
        <td style="font-size:12px;color:var(--ink-3)">${fmtTime(a.created_at)}</td>
        <td style="text-align:right;white-space:nowrap">
          <button class="btn" style="padding:4px 10px;font-size:12px" data-trace="${a.asset_id}">溯源</button>
          ${a.audit_status === 'approved'
            ? `<button class="btn" style="padding:4px 10px;font-size:12px" data-license="${a.asset_id}">授权</button>` : ''}
          ${a.audit_status === 'draft' || a.audit_status === 'rejected'
            ? `<button class="btn primary" style="padding:4px 10px;font-size:12px" data-analyze="${a.asset_id}">AI 分析</button>` : ''}
        </td>
      </tr>`).join('');
  } catch (e) {
    body.innerHTML = `<tr><td colspan="8"><div class="err">${esc(e.message)}</div></td></tr>`;
  }
}

/* ---------------------------------------------------------------- 专家工作台 */
async function loadPending() {
  const body = $('pendingBody');
  body.innerHTML = '<tr><td colspan="9"><div class="empty">加载中…</div></td></tr>';
  try {
    const [rows, ledger] = await Promise.all([req('/api/audit/pending'), req('/api/ledger')]);
    S.pending = rows;
    renderWorkbenchStats(ledger.summary);

    $('pendingSub').textContent = rows.length
      ? `共 ${rows.length} 件待终审 · 数据源：视图 v_asset_pending_audit`
      : '数据源：视图 v_asset_pending_audit';

    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="9"><div class="empty"><div class="ico">✓</div>队列已清空，没有待终审资产</div></td></tr>';
      return;
    }

    body.innerHTML = rows.map((r) => {
      const sim = r.top1_similarity;
      const simCls = sim >= 0.92 ? 'hi' : sim >= 0.8 ? 'mid' : 'lo';
      return `
      <tr>
        <td class="num">${r.asset_id}</td>
        <td><b>${esc(r.title)}</b></td>
        <td style="font-size:12px">${esc(r.owner_name)}<br>
            <span style="color:var(--ink-3);font-size:11px">${esc(r.owner_org || '')}</span></td>
        <td>${badgeLevel(r.ai_suggest_level)}</td>
        <td class="num">${r.ai_confidence != null ? (r.ai_confidence * 100).toFixed(1) + '%' : '—'}</td>
        <td style="font-size:12px">${esc(r.top1_motif_name || '—')}</td>
        <td style="min-width:90px">
          ${sim != null ? `<div class="bar"><i class="${simCls}" style="width:${Math.max(3, sim * 100)}%"></i></div>
          <span style="font-size:11px;font-family:var(--mono);color:var(--ink-3)">${sim.toFixed(4)}</span>` : '—'}
        </td>
        <td style="font-size:12px;color:var(--ink-3)">${fmtTime(r.submitted_at)}</td>
        <td style="text-align:right;white-space:nowrap">
          <button class="btn primary" style="padding:4px 11px;font-size:12px" data-audit="${r.asset_id}">终审</button>
        </td>
      </tr>`;
    }).join('');
  } catch (e) {
    body.innerHTML = `<tr><td colspan="9"><div class="err">${esc(e.message)}</div></td></tr>`;
  }
}

function renderWorkbenchStats(sum) {
  const agree = sum.ai_agree_rate != null ? (sum.ai_agree_rate * 100).toFixed(0) + '%' : '—';
  // 后端按级别/状态分组统计时，**只返回实际存在的分组**（GROUP BY 的天然行为）。
  // 例如还没有任何已确权资产时，by_status 里根本没有 approved 这个键 ——
  // 直接取 sum.by_status.approved 会得到 undefined，界面上就显示成 "undefined 件"。
  // 所以这里统一走模块级 nz() 兜底成 0。
  const byStatus = sum.by_status || {};
  const byLevel = sum.by_level || {};

  $('wbStats').innerHTML = `
    <div class="stat">
      <div class="k">待终审</div>
      <div class="v" style="color:var(--warn)">${nz(sum.pending_count)}<small>件</small></div>
      <div class="x">超过 3 日未处理的建议优先看</div>
    </div>
    <div class="stat">
      <div class="k">已确权资产</div>
      <div class="v">${nz(byStatus.approved)}<small>件</small></div>
      <div class="x">其中公有母题 ${nz(byLevel.public)} / 衍生 ${nz(byLevel.derived)} / 原创 ${nz(byLevel.original)}</div>
    </div>
    <div class="stat">
      <div class="k">AI 与专家一致率</div>
      <div class="v">${agree}</div>
      <div class="x">用于评估模型阈值是否需重新标定</div>
    </div>`;
}

/* ---------------------------------------------------------------- 终审模态 */
async function openAudit(assetId) {
  const item = S.pending.find((p) => p.asset_id === assetId);
  if (!item) return;
  S.currentAudit = item;
  $('amTitle').textContent = `终审 · 资产 #${assetId}`;
  $('amBody').innerHTML = '<div class="empty">加载相似度报告…</div>';
  $('auditModal').classList.add('on');

  let detail = null;
  try { detail = await req(`/api/asset/${assetId}`); } catch (e) { /* 忽略，用列表数据兜底 */ }

  const sims = item.similarity_json || [];
  const bars = sims.map((h) => {
    const cls = h.similarity >= 0.92 ? 'hi' : h.similarity >= 0.8 ? 'mid' : 'lo';
    return `<div class="simb"><div>
        <div class="lab"><b>${esc(h.name)} <span style="color:var(--ink-3);font-weight:400">${esc(h.code || '')}</span></b>
        <span>${Number(h.similarity).toFixed(4)}</span></div>
        <div class="bar"><i class="${cls}" style="width:${Math.max(2, h.similarity * 100)}%"></i></div>
      </div></div>`;
  }).join('') || '<div style="font-size:12px;color:var(--ink-3)">无相似度快照</div>';

  const options = ['public', 'derived', 'original'].map((lv) => `
    <label class="radio-card ${item.ai_suggest_level === lv ? 'sel' : ''}" data-lv="${lv}">
      <input type="radio" name="finalLevel" value="${lv}" ${item.ai_suggest_level === lv ? 'checked' : ''}>
      <span>
        <b>${LEVEL[lv]}${item.ai_suggest_level === lv ? '<span class="rec">AI 建议</span>' : ''}</b>
        <span>${{ public: '属公有领域传统纹样，任何人不得独占确权',
                  derived: '在公有母题基础上有实质改编，享演绎著作权',
                  original: '与现存公有母题无实质重合，可认定为原创' }[lv]}</span>
      </span>
    </label>`).join('');

  $('amBody').innerHTML = `
    <div style="display:flex;gap:16px;margin-bottom:18px;align-items:flex-start;flex-wrap:wrap">
      <img src="/api/asset/${assetId}/image" alt="作品"
           style="width:170px;height:170px;object-fit:cover;border-radius:8px;border:1px solid var(--line);background:#fff"
           onerror="this.style.display='none'">
      <div style="flex:1;min-width:220px">
        <h4 style="font-family:var(--serif);font-size:17px;margin-bottom:5px">${esc(item.title)}</h4>
        <div style="font-size:12px;color:var(--ink-2);line-height:1.9">
          权利人：${esc(item.owner_name)}　${esc(item.owner_org || '')}<br>
          上传时间：${fmtTime(item.uploaded_at)}<br>
          当前状态：${badgeStatus('pending')}
          ${detail ? `　当前权属：${badgeLevel(detail.right_level)}` : ''}
        </div>
      </div>
    </div>

    <div class="ai-panel">
      <div class="hd">
        <span class="tag">AI 建议</span>${badgeLevel(item.ai_suggest_level)}
        <span style="font-size:11px;color:var(--ink-3);margin-left:auto">
          置信度 ${item.ai_confidence != null ? (item.ai_confidence * 100).toFixed(1) : '—'}%　
          模型 ${esc(item.ai_model_version || '—')}
        </span>
      </div>
      <div class="bd"><div class="simbars">${bars}</div></div>
    </div>

    <div style="font-size:12px;font-weight:600;margin:16px 0 9px">请做出终审结论</div>
    <div class="radio-cards" id="levelOptions">${options}</div>

    <div class="field" style="margin-top:16px">
      <label>专家意见 <span style="color:var(--ink-3);font-weight:400">（写入审核流水，不可修改）</span></label>
      <textarea id="auditOpinion" placeholder="例如：与「蝴蝶纹」母题相比，翼形结构有明显个性化改良，保留传统配色……"></textarea>
    </div>

    <div class="ok-box" style="font-size:11.5px">
      提交后将在<b>单个数据库事务</b>内完成：更新审核流水 → 更新资产权属 → 触发器写入
      <span class="code-inline">system_audit_log</span> 新旧快照。任一步失败整体回滚。
    </div>

    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:4px">
      <button class="btn" id="rejectBtn">驳回</button>
      <button class="btn primary" id="approveBtn" style="padding:8px 22px">确认终审</button>
    </div>`;

  qsa('#levelOptions .radio-card').forEach((card) => {
    card.addEventListener('click', () => {
      qsa('#levelOptions .radio-card').forEach((c) => c.classList.remove('sel'));
      card.classList.add('sel');
      qs('input', card).checked = true;
    });
  });

  $('approveBtn').addEventListener('click', () => submitAudit(assetId, true));
  $('rejectBtn').addEventListener('click', () => submitAudit(assetId, false));
}

async function submitAudit(assetId, approve) {
  const checked = qs('input[name=finalLevel]:checked');
  const opinion = $('auditOpinion').value.trim();

  if (approve && !checked) { toast('请选择一个终审级别', 'bad'); return; }
  if (!approve && !opinion) { toast('驳回时必须填写理由', 'bad'); return; }

  const btn = approve ? $('approveBtn') : $('rejectBtn');
  const old = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span> 提交中';

  try {
    if (approve) {
      const r = await req(`/api/audit/${assetId}/approve`, {
        method: 'POST',
        body: { final_level: checked.value, opinion },
      });
      toast(r.message, 'ok');
      if (r.db_audit_log_ids && r.db_audit_log_ids.length) {
        toast(`触发器已写入审计日志 #${r.db_audit_log_ids.join(', #')}`, 'ok');
      }
    } else {
      await req(`/api/audit/${assetId}/reject`, { method: 'POST', body: { opinion } });
      toast('已驳回，资产退回待修改', 'ok');
    }
    closeModal('auditModal');
    loadPending();
  } catch (e) {
    toast(e.message, 'bad');
  } finally {
    btn.disabled = false;
    btn.innerHTML = old;
  }
}

/* ---------------------------------------------------------------- 监管台账 */
async function loadLedger() {
  const body = $('ledgerBody');
  body.innerHTML = '<tr><td colspan="12"><div class="empty">加载中…</div></td></tr>';
  try {
    const r = await req('/api/ledger');
    S.ledger = r.rows;
    renderLedgerStats(r.summary);
    renderLedgerTable();
  } catch (e) {
    body.innerHTML = `<tr><td colspan="12"><div class="err">${esc(e.message)}</div></td></tr>`;
  }
}

function renderLedgerStats(sum) {
  const agree = sum.ai_agree_rate != null ? (sum.ai_agree_rate * 100).toFixed(0) + '%' : '—';
  // 同上：GROUP BY 只返回存在的分组，缺失的键要兜底成 0，否则界面显示 "undefined"
  const byLevel = sum.by_level || {};
  $('ledgerStats').innerHTML = `
    <div class="stat">
      <div class="k">在册资产</div>
      <div class="v">${nz(sum.total_assets)}<small>件</small></div>
      <div class="x">公有 ${nz(byLevel.public)} · 衍生 ${nz(byLevel.derived)} · 原创 ${nz(byLevel.original)}</div>
    </div>
    <div class="stat">
      <div class="k">授权费用合计（生效中）</div>
      <div class="v">¥${fmtMoney(nz(sum.total_license_fee_cny))}</div>
      <div class="x">来自 license_record 生效记录</div>
    </div>
    <div class="stat">
      <div class="k">审计日志条目</div>
      <div class="v">${nz(sum.db_audit_log_count)}<small>条</small></div>
      <div class="x">触发器自动写入 · AI 一致率 ${agree}</div>
    </div>`;
}

function renderLedgerTable() {
  const body = $('ledgerBody');
  const f = S.ledgerFilter;
  const rows = f === 'all' ? S.ledger : S.ledger.filter((r) => r.right_level === f);
  $('ledgerCount').textContent = `显示 ${rows.length} / ${S.ledger.length} 件`;

  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="12"><div class="empty"><div class="ico">◌</div>暂无符合条件的资产</div></td></tr>';
    return;
  }
  body.innerHTML = rows.map((r) => `
    <tr>
      <td class="num">${r.asset_id}</td>
      <td><b>${esc(r.title)}</b></td>
      <td style="font-size:12px">${esc(r.owner_name)}</td>
      <td>${badgeLevel(r.right_level)}</td>
      <td>${badgeStatus(r.audit_status)}</td>
      <td>${badgeLevel(r.ai_suggest_level)}</td>
      <td>${r.ai_expert_agree === true ? '<span class="badge approved">一致</span>'
            : r.ai_expert_agree === false ? '<span class="badge rejected">分歧</span>'
            : '<span class="badge gray">—</span>'}</td>
      <td style="font-size:12px">${esc(r.motif_name || '—')}</td>
      <td class="num">${r.license_cnt}</td>
      <td class="num">${fmtMoney(r.total_fee_cny)}</td>
      <td class="num">${r.db_log_cnt}</td>
      <td style="text-align:right;white-space:nowrap">
        <button class="btn" style="padding:4px 10px;font-size:12px" data-trace="${r.asset_id}">溯源</button>
      </td>
    </tr>`).join('');
}

/* ---------------------------------------------------------------- 溯源 */
async function openTrace(assetId) {
  $('tmTitle').textContent = `溯源链路 · 资产 #${assetId}`;
  $('tmBody').innerHTML = '<div class="empty">加载链路…</div>';
  $('traceModal').classList.add('on');

  try {
    const t = await req(`/api/asset/${assetId}/trace`);
    const valid = t.is_chain_valid
      ? `<div class="ok-box"><b>✓ 链路完整</b>　共 ${t.nodes.length} 个事件节点，哈希与授权链校验通过</div>`
      : `<div class="warn-box"><b>⚠ 链路校验发现 ${t.chain_issues.length} 处问题</b><br>
           ${t.chain_issues.map((i) => '· ' + esc(i)).join('<br>')}</div>`;

    const nodes = t.nodes.map((n, i) => `
      <div class="tl-item ${esc(n.event_type)}">
        <div class="tl-dot">${i + 1}</div>
        <div class="tl-body">
          <div class="tl-head">
            <b>${esc(n.summary)}</b>
            <span class="badge ${n.event_type === 'license' ? 'rejected'
              : n.event_type === 'audit' ? 'derived'
              : n.event_type === 'root' ? 'public' : 'gray'}">
              ${esc(EVENT_LABEL[n.event_type] || n.event_type)}</span>
            <time>${fmtTime(n.event_at)}</time>
          </div>
          <div class="tl-meta">
            操作者：${esc(n.actor_name || '系统')}
            ${n.actor_role ? `（${esc(n.actor_role)}）` : ''}
            ${n.detail && n.detail.state ? `　状态：${esc(n.detail.state)}` : ''}
            ${n.detail && n.detail.opinion ? `　意见：${esc(n.detail.opinion)}` : ''}
          </div>
          <details>
            <summary>查看节点数据快照</summary>
            <pre class="json">${esc(JSON.stringify(n.detail, null, 2))}</pre>
          </details>
        </div>
      </div>`).join('');

    $('tmBody').innerHTML = `
      ${valid}
      <dl class="kv" style="margin-bottom:18px">
        <dt>作品名称</dt><dd style="font-family:inherit">${esc(t.title)}</dd>
        <dt>权属级别</dt><dd>${badgeLevel(t.right_level)}</dd>
        <dt>资产状态</dt><dd>${badgeStatus(t.audit_status)}</dd>
        <dt>文件摘要</dt><dd>${esc(t.file_hash)}</dd>
      </dl>
      <div class="timeline">${nodes}</div>`;
  } catch (e) {
    $('tmBody').innerHTML = `<div class="err">${esc(e.message)}</div>`;
  }
}

/* ---------------------------------------------------------------- 授权 */
async function openLicense(assetId) {
  $('lmBody').innerHTML = '<div class="empty">加载中…</div>';
  $('licenseModal').classList.add('on');

  // 平台内用户下拉只有专家/管理员才有权限拉（/api/users 限 expert+admin）。
  // 传承人进授权弹窗时不去请求，否则控制台会留下一条无意义的 403。
  let users = [];
  if (['expert', 'admin'].includes(S.user && S.user.role)) {
    try { users = await req('/api/users?role=admin'); } catch (e) { /* 忽略 */ }
  }

  const today = new Date().toISOString().slice(0, 10);
  $('lmBody').innerHTML = `
    <div class="field">
      <label>被授权方类型</label>
      <select id="lcType">
        <option value="org">外部机构（填写名称）</option>
        <option value="user">平台内用户</option>
      </select>
    </div>
    <div class="field" id="lcOrgWrap">
      <label>机构名称 <span class="req">*</span></label>
      <input type="text" id="lcOrg" placeholder="例如：黔东南州博物馆">
    </div>
    <div class="field" id="lcUserWrap" style="display:none">
      <label>选择用户</label>
      <select id="lcUser">
        ${users.map((u) => `<option value="${u.user_id}">${esc(u.display_name)}（${esc(u.org_name || u.role)}）</option>`).join('')}
      </select>
    </div>
    <div class="grid2" style="gap:12px">
      <div class="field">
        <label>授权类型</label>
        <select id="lcLicenseType">
          <option value="non_exclusive">非独占</option>
          <option value="exclusive">独占</option>
          <option value="display">展示</option>
          <option value="derivative">改编</option>
        </select>
      </div>
      <div class="field">
        <label>地域范围</label>
        <input type="text" id="lcTerritory" placeholder="如：中国大陆">
      </div>
    </div>
    <div class="grid2" style="gap:12px">
      <div class="field">
        <label>起始日期 <span class="req">*</span></label>
        <input type="date" id="lcStart" value="${today}">
      </div>
      <div class="field">
        <label>终止日期</label>
        <input type="date" id="lcEnd">
      </div>
    </div>
    <div class="field">
      <label>授权费用（元）</label>
      <input type="number" id="lcFee" min="0" step="100" placeholder="0">
    </div>
    <div class="field">
      <label>授权范围说明</label>
      <textarea id="lcScope" placeholder="如：用于文创产品开发，限 5000 件"></textarea>
    </div>
    <div class="ok-box" style="font-size:11.5px">
      授权记录为<b>追加式</b>：一经写入不可修改或删除，撤销需新增一条 revoked 记录，保证流转链可追溯。
    </div>
    <div style="display:flex;gap:10px;justify-content:flex-end">
      <button class="btn" data-close="licenseModal">取消</button>
      <button class="btn primary" id="lcSubmit" style="padding:8px 20px">登记授权</button>
    </div>`;

  $('lcType').addEventListener('change', (e) => {
    const isOrg = e.target.value === 'org';
    $('lcOrgWrap').style.display = isOrg ? '' : 'none';
    $('lcUserWrap').style.display = isOrg ? 'none' : '';
  });

  $('lcSubmit').addEventListener('click', async () => {
    const type = $('lcType').value;
    const body = {
      license_type: $('lcLicenseType').value,
      territory: $('lcTerritory').value.trim() || null,
      start_date: $('lcStart').value,
      end_date: $('lcEnd').value || null,
      fee_cny: $('lcFee').value ? Number($('lcFee').value) : null,
      scope: $('lcScope').value.trim() || null,
    };
    if (type === 'org') {
      body.licensee_name = $('lcOrg').value.trim();
      if (!body.licensee_name) { toast('请填写机构名称', 'bad'); return; }
    } else {
      body.licensee_user_id = Number($('lcUser').value);
    }
    if (!body.start_date) { toast('请选择起始日期', 'bad'); return; }

    const btn = $('lcSubmit');
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> 提交中';
    try {
      await req(`/api/asset/${assetId}/license`, { method: 'POST', body });
      toast('授权记录已登记', 'ok');
      closeModal('licenseModal');
      loadMine();
    } catch (e) {
      toast(e.message, 'bad');
    } finally {
      btn.disabled = false;
      btn.textContent = '登记授权';
    }
  });
}

/* ---------------------------------------------------------------- 模态通用 */
function closeModal(id) {
  $(id).classList.remove('on');
}

/* ---------------------------------------------------------------- 事件绑定 */
function bindEvents() {
  $('loginBtn').addEventListener('click', doLogin);
  $('loginPass').addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });
  $('logoutBtn').addEventListener('click', () => logout());

  qsa('#tabs button').forEach((b) => {
    b.addEventListener('click', () => switchPage(b.dataset.page));
  });

  // 模态关闭
  qsa('[data-close]').forEach((el) => {
    el.addEventListener('click', () => closeModal(el.dataset.close));
  });
  qsa('.modal-bg').forEach((bg) => {
    bg.addEventListener('click', (e) => { if (e.target === bg) bg.classList.remove('on'); });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') qsa('.modal-bg.on').forEach((m) => m.classList.remove('on'));
  });

  // 事件委托：表格里的操作按钮（内容重渲染后依然有效）
  document.addEventListener('click', async (e) => {
    const t = e.target.closest('[data-trace],[data-audit],[data-license],[data-analyze]');
    if (!t) return;
    if (t.dataset.trace) openTrace(Number(t.dataset.trace));
    else if (t.dataset.audit) openAudit(Number(t.dataset.audit));
    else if (t.dataset.license) openLicense(Number(t.dataset.license));
    else if (t.dataset.analyze) {
      const id = Number(t.dataset.analyze);
      t.disabled = true;
      t.innerHTML = '<span class="spin dark"></span>';
      try {
        const an = await req(`/api/asset/${id}/analyze`, { method: 'POST' });
        toast(`资产 #${id} AI 建议：${LEVEL[an.suggested_level]}（${(an.confidence * 100).toFixed(1)}%）`, 'ok');
        loadMine();
      } catch (err) {
        toast(err.message, 'bad');
        t.disabled = false;
        t.textContent = 'AI 分析';
      }
    }
  });

  // 台账筛选
  qsa('#ledgerFilter button').forEach((b) => {
    b.addEventListener('click', () => {
      qsa('#ledgerFilter button').forEach((x) => x.classList.remove('on'));
      b.classList.add('on');
      S.ledgerFilter = b.dataset.f;
      renderLedgerTable();
    });
  });

  // 母题筛选
  qsa('#motifFilter button').forEach((b) => {
    b.addEventListener('click', () => {
      qsa('#motifFilter button').forEach((x) => x.classList.remove('on'));
      b.classList.add('on');
      S.motifFilter = b.dataset.f;
      renderMotifGrid();
    });
  });
}

/* ---------------------------------------------------------------- 演示账号 */
function renderQuickLogin() {
  const accounts = [
    { u: 'heritor01', n: '杨秀英', r: 'heritor', label: '传承人', color: 'var(--green-soft)', fg: 'var(--green)' },
    { u: 'expert01', n: '周文渊', r: 'expert', label: '专家', color: 'var(--indigo-soft)', fg: 'var(--indigo)' },
    { u: 'admin01', n: '陈立', r: 'admin', label: '监管', color: 'var(--gold-soft)', fg: 'var(--gold)' },
  ];
  const wrap = $('quickLogin');
  accounts.forEach((a) => {
    const b = document.createElement('button');
    b.className = 'qrow';
    b.innerHTML = `
      <span>
        <b>${a.n}</b>
        <span>${a.u} / Passw0rd!</span>
      </span>
      <span class="qrole" style="background:${a.color};color:${a.fg}">${a.label}</span>`;
    b.addEventListener('click', () => {
      $('loginUser').value = a.u;
      $('loginPass').value = 'Passw0rd!';
      $('loginErr').textContent = '';
    });
    wrap.appendChild(b);
  });
}

/* ---------------------------------------------------------------- 启动 */
(async function boot() {
  bindEvents();
  initUpload();
  renderQuickLogin();

  if (S.token && S.user) {
    // 有缓存令牌：先验证有效性，避免直接把用户带进一个 401 的界面
    try {
      await req('/api/auth/me');
      enterApp();
    } catch (e) {
      logout(true);
    }
  }

  // 后端可用性提示
  try {
    const h = await fetch('/api/health').then((r) => r.json());
    if (!h.ok) {
      console.error('数据库未就绪：', h.database);
      const box = $('loginErr');
      box.textContent = '数据库未就绪：' + h.database + '。请按 README 完成初始化后重启服务。';
      box.style.display = 'block';
    }
  } catch (e) {
    console.error('后端未启动');
  }
})();
