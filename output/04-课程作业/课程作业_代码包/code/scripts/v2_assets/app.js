/* =========================================================
   互动演示版 · 核心层
   数据 / 工具 / 登录 / 框架 / 通用组件
   ========================================================= */

const SEED = window.__SEED__;
const IMG = window.__IMG__;
const BUILT_AT = window.__BUILT__;

const USERS = SEED.users;
const MOTIFS = SEED.motifs;

/* 作品是可变的：终审会就地改 right_level / audit_status，重置靠深拷贝还原 */
let ASSETS = JSON.parse(JSON.stringify(SEED.assets));
const ASSETS_ORIGINAL = JSON.parse(JSON.stringify(SEED.assets));

const LEVEL = { original: '原创', derived: '改编衍生', public: '公有母题', undecided: '未定' };
const STATUS = { approved: '已确权', pending: '待审', draft: '草稿', rejected: '已驳回' };
const ROLE = { heritor: '传承人', expert: '评审专家', admin: '监管管理员' };
/* 四种，与 sql/01_init.sql 的 ck_lic_type 保持一致 */
const LICENSE_TYPE = {
  exclusive: '独占授权', non_exclusive: '非独占授权',
  display: '展示授权', derivative: '衍生开发授权',
};

/* 页面定义：哪个角色能看到哪个页面。和真实系统的角色校验一一对应 */
const PAGES = {
  dashboard: { label: '概览',           crumb: '工作台 / 概览',           roles: ['heritor', 'expert', 'admin'] },
  assets:    { label: '作品管理',       crumb: '工作台 / 作品管理',       roles: ['heritor', 'expert', 'admin'] },
  motifs:    { label: '母题库',         crumb: '工作台 / 母题库',         roles: ['heritor', 'expert', 'admin'] },
  upload:    { label: '上传与 AI 分析', crumb: '工作台 / 上传与 AI 分析', roles: ['heritor', 'expert', 'admin'] },
  review:    { label: '专家工作台',     crumb: '审核 / 专家工作台',       roles: ['expert', 'admin'] },
  ledger:    { label: '监管台账',       crumb: '审核 / 监管台账',         roles: ['expert', 'admin'] },
  trace:     { label: '溯源图谱',       crumb: '审核 / 溯源图谱',         roles: ['heritor', 'expert', 'admin'] },
  audit:     { label: '审计日志',       crumb: '审核 / 审计日志',         roles: ['expert', 'admin'] },
  settings:  { label: '演示设置',       crumb: '系统 / 演示设置',         roles: ['heritor', 'expert', 'admin'] },
};

/* 全局状态。所有筛选条件与选择都放这里，渲染函数只读它 */
const S = {
  user: null,
  page: 'dashboard',
  // 作品管理
  aQ: '', aLv: 'all', aSt: 'all', aSort: 'id', aView: 'table', aSel: new Set(),
  // 母题库
  mQ: '', mEthnic: new Set(), mCat: 'all', mEra: 'all',
  // 上传
  upPick: null, upMotif: 'auto', upResult: null, upBusy: false,
  // 终审
  rvPick: null, rvLevel: null, rvOpinion: '', rvWeight: 70,
  // 台账
  lType: 'all', lScope: 'all', lSort: 'fee',
  // 溯源
  tPick: null,
  // 审计
  auType: 'all',
  // 概览
  dashRange: 'month',
  // 演示设置
  cfg: { compact: false, showConf: true, anim: true, showHint: true },
  thrD: 0.80, thrO: 0.92,
  // 登录过程
  accPick: null, sliderDone: false, remember: false,
  userPickPage: null, pageSize: 20,
};

/* ============================ 工具 ============================ */

const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const fmtMoney = n => '¥' + Number(n || 0).toLocaleString('zh-CN');
const fmtPct = n => (Number(n || 0) * 100).toFixed(1) + '%';
const fmtSim = n => Number(n || 0).toFixed(4);

/* 头像底色按 user_id 固定，同一个人在哪都是同一个颜色 */
const AV_COLORS = ['#9d2f2a', '#2f5d7c', '#2f6b4f', '#a8661c'];
const avColor = uid => AV_COLORS[(uid - 1) % AV_COLORS.length];
const initial = name => (name || '?').slice(0, 1);

const motifOf = id => MOTIFS.find(m => m.motif_id === id) || null;
const assetImg = a => IMG['asset_' + a.asset_id] || '';
const motifImg = m => IMG[m.motif_code] || '';

function lvTag(lv) {
  const map = { original: 'lv-original', derived: 'lv-derived', public: 'lv-public', undecided: 'lv-undecided' };
  return `<span class="tag ${map[lv] || 'lv-undecided'}">${LEVEL[lv] || '未定'}</span>`;
}
function stTag(st) {
  const map = { approved: 'st-approved', pending: 'st-pending', draft: 'st-draft', rejected: 'st-rejected' };
  return `<span class="tag ${map[st] || 'st-draft'}">${STATUS[st] || st}</span>`;
}

let toastTimer = null;
function toast(msg) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2200);
}

/* 当前身份能看到的作品：传承人只看自己的，专家与管理员看全部 */
function visibleAssets() {
  if (!S.user) return [];
  if (S.user.role === 'heritor') return ASSETS.filter(a => a.owner_id === S.user.user_id);
  return ASSETS.slice();
}

function pendingList() {
  return visibleAssets().filter(a => a.audit_status === 'pending');
}

/* 所有授权，扁平化后带上作品信息，台账与统计都用它 */
function allLicenses() {
  const out = [];
  visibleAssets().forEach(a => {
    (a.licenses || []).forEach(l => {
      out.push(Object.assign({}, l, {
        asset_id: a.asset_id,
        asset_title: a.title,
        owner_id: a.owner_id,
      }));
    });
  });
  return out;
}

function isExpired(l) {
  if (!l.end_date) return false;
  return new Date(l.end_date) < new Date('2026-10-30');
}

function ownerName(uid) {
  const u = USERS.find(x => x.user_id === uid);
  return u ? u.display_name : '未知';
}

/* ============================ 登录 ============================ */

function renderLoginAccounts() {
  const box = $('accGrid');
  box.innerHTML = USERS.map(u => `
    <button class="acc" data-uid="${u.user_id}">
      <span class="av" style="background:${avColor(u.user_id)}">${esc(initial(u.display_name))}</span>
      <span>
        <span class="nm">${esc(u.display_name)}</span>
        <span class="rl">${ROLE[u.role] || u.role}</span>
      </span>
      <span class="tick">✓</span>
    </button>`).join('');

  box.querySelectorAll('.acc').forEach(b => {
    b.onclick = () => {
      S.accPick = Number(b.dataset.uid);
      box.querySelectorAll('.acc').forEach(x => x.classList.toggle('on', x === b));
      hideErr();
      syncSubmit();
    };
  });
}

function showErr(msg) {
  const el = $('lgErr');
  el.textContent = msg;
  el.classList.add('show');
}
function hideErr() { $('lgErr').classList.remove('show'); }

function syncSubmit() {
  const btn = $('lgSubmit');
  const ok = S.accPick && S.sliderDone;
  btn.disabled = !ok;
  btn.textContent = ok ? '登 录' : (S.sliderDone ? '请选择登录账号' : '请先拖动滑块完成验证');
}

/* 拖动滑块：指针拖到 95% 以上才算通过，中途松手回弹。
   用 pointer 事件同时覆盖鼠标与触屏，比分别绑 mousedown/touchstart 干净。 */
function initSlider() {
  const wrap = $('slider');
  const knob = $('slKnob');
  const fill = $('slFill');
  const txt = $('slTxt');
  let dragging = false;
  let startX = 0;
  let curX = 0;
  let maxX = 0;

  function maxTravel() {
    // 轨道内可移动距离 = 轨道宽 - 滑块宽 - 左右各 3px 内边距
    return wrap.clientWidth - knob.offsetWidth - 6;
  }

  function paint(x) {
    const pct = maxX > 0 ? Math.max(0, Math.min(1, x / maxX)) : 0;
    knob.style.transform = `translateX(${pct * maxX}px)`;
    fill.style.width = (pct * 100) + '%';
  }

  knob.addEventListener('pointerdown', e => {
    if (S.sliderDone) return;
    dragging = true;
    maxX = maxTravel();
    startX = e.clientX;
    wrap.classList.add('dragging');
    knob.setPointerCapture(e.pointerId);
  });

  knob.addEventListener('pointermove', e => {
    if (!dragging) return;
    curX = Math.max(0, Math.min(maxX, e.clientX - startX));
    paint(curX);
    txt.textContent = curX >= maxX * 0.95 ? '松开完成验证' : '继续向右拖动';
  });

  function endDrag(e) {
    if (!dragging) return;
    dragging = false;
    wrap.classList.remove('dragging');
    maxX = maxTravel();
    if (curX >= maxX * 0.95) {
      S.sliderDone = true;
      wrap.classList.add('done');
      paint(maxX);
      knob.textContent = '✓';
      txt.textContent = '验证通过';
      hideErr();
    } else {
      curX = 0;
      paint(0);
      txt.textContent = '按住滑块，拖到最右侧';
    }
    syncSubmit();
  }

  knob.addEventListener('pointerup', endDrag);
  knob.addEventListener('pointercancel', endDrag);

  // 重置滑块（退出登录时用）
  window.__resetSlider = function () {
    S.sliderDone = false;
    curX = 0;
    wrap.classList.remove('done', 'dragging');
    paint(0);
    knob.textContent = '›';
    txt.textContent = '按住滑块，拖到最右侧';
    syncSubmit();
  };
}

function doLogin() {
  const u = USERS.find(x => x.user_id === S.accPick);
  if (!u) { showErr('请先选择一个登录账号'); return; }
  const pw = $('pw').value;
  if (!pw) { showErr('请输入密码'); return; }
  if (pw !== u.password) { showErr('密码不正确。演示账号密码为 Passw0rd!'); return; }
  if (!S.sliderDone) { showErr('请先完成滑块验证'); return; }

  S.user = u;
  hideErr();

  // 进场
  $('login').style.display = 'none';
  $('app').classList.add('on');
  enterApp();
}

/* ============================ 应用框架 ============================ */

function enterApp() {
  const u = S.user;

  $('meAv').textContent = initial(u.display_name);
  $('meAv').style.background = avColor(u.user_id);
  $('meNm').textContent = u.display_name;
  $('meOg').textContent = u.org_name || ROLE[u.role];

  renderNav();
  go(S.userPickPage || roleLanding());
  renderBadges();
}

/* 按角色显示/隐藏导航项。真实系统里这是后端 require_roles 拦的，
   前端隐藏只是体验优化，不是安全边界——这一点在文档里写清楚了。 */
function renderNav() {
  document.querySelectorAll('#nav button[data-pg]').forEach(b => {
    const pg = b.dataset.pg;
    const def = PAGES[pg];
    b.style.display = (def && def.roles.includes(S.user.role)) ? '' : 'none';
  });
}

/* 角色默认落地页。与用户在设置里自选的 defPage 分开：
   否则传承人登录后会被写成 review，而他根本无权访问。 */
function roleLanding() {
  if (S.user.role === 'heritor') return 'assets';
  if (S.user.role === 'expert') return 'review';
  return 'dashboard';
}

function go(pg) {
  if (!PAGES[pg]) return;
  if (!PAGES[pg].roles.includes(S.user.role)) { toast('当前身份无权访问该页面'); return; }

  S.page = pg;
  document.querySelectorAll('#nav button[data-pg]').forEach(b =>
    b.classList.toggle('on', b.dataset.pg === pg));
  document.querySelectorAll('.page').forEach(p =>
    p.classList.toggle('on', p.id === 'pg-' + pg));

  $('crumb').innerHTML = PAGES[pg].crumb.replace(/\//g, '<span style="opacity:.4"> / </span>');

  // 每一页的渲染入口
  const R = {
    dashboard: renderDashboard, assets: renderAssets, motifs: renderMotifs,
    upload: renderUpload, review: renderReview, ledger: renderLedger,
    trace: renderTrace, audit: renderAudit, settings: renderSettings,
  }[pg];
  if (R) R();

  $('body').scrollTop = 0;
  document.querySelector('main').scrollTop = 0;
}

function renderBadges() {
  const pend = pendingList().length;
  const mine = visibleAssets().length;
  const ba = $('bdgAssets'), br = $('bdgReview');
  ba.textContent = mine || '';
  ba.style.display = mine ? '' : 'none';
  br.textContent = pend || '';
  br.style.display = pend ? '' : 'none';
  $('btnBell').innerHTML = pend ? `◔<span class="dot">${pend}</span>` : '◔';
}

function logout() {
  S.user = null;
  S.aSel.clear();
  S.rvPick = null; S.rvLevel = null; S.rvOpinion = '';
  S.upPick = null; S.upResult = null;
  S.accPick = null;
  $('meMenu').classList.remove('show');
  $('app').classList.remove('on');
  $('login').style.display = '';
  document.querySelectorAll('#accGrid .acc').forEach(x => x.classList.remove('on'));
  if (window.__resetSlider) window.__resetSlider();
  syncSubmit();
  toast('已退出登录');
}

/* ============================ 通用组件 ============================ */

/* 相似度条。阈值刻度线用来直观说明「这条线以上判公有」 */
function simBar(label, value, hi, thr) {
  const w = Math.max(2, Math.min(100, value * 100));
  const thrStyle = thr ? `left:${thr * 100}%` : '';
  return `<div class="bar">
    <span class="lb" title="${esc(label)}">${esc(label)}</span>
    <span class="track">
      <span class="fill ${hi ? 'hi' : ''}" style="width:${w}%"></span>
      ${thr ? `<span class="thr" style="${thrStyle}"></span>` : ''}
    </span>
    <span class="pct">${fmtSim(value)}</span>
  </div>`;
}

function openDrawer(title, bodyHtml, footHtml) {
  $('dTitle').textContent = title;
  $('dBody').innerHTML = bodyHtml;
  $('dFoot').innerHTML = footHtml || '';
  $('dMask').classList.add('show');
  $('drawer').classList.add('show');
}
function closeDrawer() {
  $('dMask').classList.remove('show');
  $('drawer').classList.remove('show');
}
