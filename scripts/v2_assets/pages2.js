/* =========================================================
   互动演示版 · 页面层（二）
   上传分析 / 专家终审 / 监管台账 / 溯源图谱 / 审计日志 / 设置 / 启动
   ========================================================= */

const TODAY = '2026-10-30';

/* 相似度落在哪个档位——这是系统的核心判定规则，阈值可在设置页拖动 */
function judgeBySim(sim) {
  if (sim < S.thrD) return 'original';
  if (sim < S.thrO) return 'derived';
  return 'public';
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

/* SVG 里没有 text-overflow，只能在字符串层面截断 */
function shortTitle(s, n) {
  s = String(s || '');
  return s.length > n ? s.slice(0, n) + '…' : s;
}

/* ============================ 上传与 AI 分析 ============================ */

function renderUpload() {
  const list = visibleAssets().filter(a => (a.audits || []).length);
  const box = $('upSamples');

  box.innerHTML = list.map(a => `
    <button class="sample ${S.upPick === a.asset_id ? 'on' : ''}" data-aid="${a.asset_id}">
      ${assetImg(a) ? `<img src="${assetImg(a)}" alt="">` : ''}
      <span>
        <span class="nm">${esc(a.title)}</span>
        <span class="sub">NO-${String(a.asset_id).padStart(3, '0')}　v${a.version_no}</span>
      </span>
      <span class="pick">已选</span>
    </button>`).join('') || `<div class="empty"><div class="ic">◌</div>当前身份下没有已分析过的作品</div>`;

  box.querySelectorAll('.sample').forEach(b => {
    b.onclick = () => {
      S.upPick = Number(b.dataset.aid);
      S.upResult = null;
      renderUpload();
    };
  });

  // 母题下拉只初始化一次
  const ms = $('upMotif');
  if (ms.options.length <= 1) {
    MOTIFS.forEach(m => {
      ms.insertAdjacentHTML('beforeend',
        `<option value="${m.motif_id}">${esc(m.motif_code + ' ' + m.name)}（${esc(m.ethnic)}）</option>`);
    });
  }

  $('btnAnalyze').disabled = !S.upPick || S.upBusy;

  if (!S.upResult) {
    if (!$('upResult').dataset.busy) {
      $('upResult').innerHTML = `<div class="empty"><div class="ic">◌</div>尚未分析，请先在左侧选择一张样例</div>`;
      $('upResultTag').textContent = '';
    }
    return;
  }
  paintUpResult();
}

async function runAnalyze() {
  if (!S.upPick || S.upBusy) return;
  const a = ASSETS.find(x => x.asset_id === S.upPick);
  if (!a) return;

  S.upBusy = true;
  $('btnAnalyze').disabled = true;
  $('upResultTag').textContent = '分析中…';
  $('upResult').dataset.busy = '1';

  const steps = ['读取图像并归一化', '提取颜色 / 纹理 / 形状特征（512 维）', '在母题库中检索最相似纹样', '按阈值生成分级建议'];
  $('upResult').innerHTML = `<div class="prog" id="prog">` + steps.map((s, i) =>
    `<div class="pstep" data-i="${i}"><span class="dot">${i + 1}</span><span>${esc(s)}</span></div>`).join('') + `</div>`;

  for (let i = 0; i < steps.length; i++) {
    const el = document.querySelector(`#prog [data-i="${i}"]`);
    if (el) el.classList.add('on');
    await sleep(420);
    if (el) { el.classList.remove('on'); el.classList.add('done'); el.querySelector('.dot').textContent = '✓'; }
  }

  await sleep(200);

  // 结果全部取自种子里真实算法写过的那条记录，不在这里编数字
  const rec = (a.audits || []).filter(x => x.similarity).slice(-1)[0] || {};
  S.upResult = {
    sim: rec.similarity || 0,
    motifId: rec.top_motif_id || a.motif_id,
    suggest: a.ai_suggest_level,
    conf: a.ai_confidence,
    byThr: judgeBySim(rec.similarity || 0),
  };

  S.upBusy = false;
  delete $('upResult').dataset.busy;
  $('upResultTag').textContent = '完成';
  paintUpResult();
  renderUpload();
}

function paintUpResult() {
  const a = ASSETS.find(x => x.asset_id === S.upPick);
  const r = S.upResult;
  if (!a || !r) return;
  const m = r.motifId ? motifOf(r.motifId) : null;
  const same = r.byThr === r.suggest;

  $('upResult').innerHTML = `
    <div class="vs" style="margin-bottom:14px">
      <div class="box ai">
        <div class="t">AI 建议档位</div>
        <div class="big">${LEVEL[r.suggest] || '—'}</div>
        <div class="n">置信度 ${fmtPct(r.conf)}</div>
      </div>
      <div class="box human">
        <div class="t">按当前阈值推算</div>
        <div class="big">${LEVEL[r.byThr]}</div>
        <div class="n">${same ? '与 AI 建议一致' : '与 AI 建议不一致'}</div>
      </div>
    </div>

    <div class="sec-t">最相似母题</div>
    ${m ? `
      <div style="display:flex;gap:10px;align-items:center;margin-bottom:8px">
        ${motifImg(m) ? `<img src="${motifImg(m)}" style="width:46px;height:46px;border-radius:6px;object-fit:cover;border:1px solid var(--line)">` : ''}
        <div>
          <div style="font-size:12.5px;font-weight:700">${esc(m.motif_code)}　${esc(m.name)}</div>
          <div style="font-size:11px;color:var(--ink3)">${esc(m.ethnic)}　${esc(m.category)}</div>
        </div>
      </div>
      ${simBar('余弦相似度', r.sim, r.sim >= S.thrO, S.thrD)}
      <div style="font-size:10.5px;color:var(--ink4);margin-top:6px;line-height:1.7">
        竖线为「原创 / 改编」阈值 ${S.thrD.toFixed(2)}。相似度低于该值判原创，
        ${S.thrD.toFixed(2)}–${S.thrO.toFixed(2)} 判改编衍生，高于 ${S.thrO.toFixed(2)} 判公有母题。
      </div>` : `<div style="font-size:12px;color:var(--ink4)">未检出显著相似的母题</div>`}

    <div class="note-box" style="margin-top:14px">
      <b>AI 没有写入权属</b>　本次分析只产出档位建议与置信度，作品的
      <code style="font-size:11px">right_level</code> 字段仍为「${LEVEL[a.right_level]}」。
      权属只能由专家终审写入。
    </div>

    <div style="display:flex;gap:9px;margin-top:14px">
      ${a.audit_status === 'draft'
      ? `<button class="btn primary" id="btnSubmitUp" style="flex:1">提交审核</button>`
      : `<button class="btn" id="btnGoReview2" style="flex:1">查看这件作品的终审</button>`}
      <button class="btn" onclick="openAssetDetail(${a.asset_id})">查看详情</button>
    </div>
    <div style="font-size:10.5px;color:var(--ink4);margin-top:9px">
      当前仅展示真实算法写入的记录；同一次分析的其余母题相似度未进入候选，故不列出。
    </div>
  `;

  const sub = $('btnSubmitUp');
  if (sub) sub.onclick = () => {
    a.audit_status = 'pending';
    toast('已提交审核，转入专家工作台');
    S.upResult = null;
    renderBadges();
    renderUpload();
  };

  const gr = $('btnGoReview2');
  if (gr) gr.onclick = () => {
    if (a.audit_status === 'pending' && PAGES.review.roles.includes(S.user.role)) {
      S.rvPick = a.asset_id;
      go('review');
    } else {
      openAssetDetail(a.asset_id);
    }
  };
}

/* ============================ 专家工作台 ============================ */

function renderReview() {
  const pend = pendingList();
  $('rvCount').textContent = `${pend.length} 件待审`;
  $('reviewActions').innerHTML = pend.length
    ? `<button class="btn sm" id="btnPassAll">全部按 AI 建议通过</button>` : '';

  $('rvList').innerHTML = pend.length ? pend.map(a => `
    <button class="rv-item ${S.rvPick === a.asset_id ? 'on' : ''}" data-aid="${a.asset_id}">
      ${assetImg(a) ? `<img src="${assetImg(a)}" alt="">` : ''}
      <span>
        <span class="nm">${esc(a.title)}</span>
        <span class="sub">AI：${LEVEL[a.ai_suggest_level]}　v${a.version_no}</span>
      </span>
    </button>`).join('')
    : `<div class="empty"><div class="ic">✓</div>队列已清空</div>`;

  $('rvList').querySelectorAll('.rv-item').forEach(b => {
    b.onclick = () => {
      S.rvPick = Number(b.dataset.aid);
      S.rvLevel = null;
      S.rvOpinion = '';
      renderReview();
    };
  });

  const passAll = $('btnPassAll');
  if (passAll) passAll.onclick = () => {
    let n = 0, ov = 0;
    pend.forEach(a => {
      if (approveAsset(a, a.ai_suggest_level, '按 AI 建议批量通过')) {
        n++;
        if (a.ai_suggest_level !== a.right_level) ov++;
      }
    });
    S.rvPick = null;
    toast(`已通过 ${n} 件`);
    renderBadges();
    renderReview();
  };

  // 右侧
  const a = pend.find(x => x.asset_id === S.rvPick) || ASSETS.find(x => x.asset_id === S.rvPick);
  if (!a || a.audit_status !== 'pending') {
    $('rvTitle').textContent = '未选择作品';
    $('rvBody').innerHTML = `<div class="empty"><div class="ic">◌</div>请从左侧待审队列中选择一件作品</div>`;
    return;
  }

  $('rvTitle').textContent = a.title;

  const m = a.motif_id ? motifOf(a.motif_id) : null;
  const rec = (a.audits || []).filter(x => x.similarity).slice(-1)[0] || {};
  const sim = rec.similarity || 0;
  const byThr = judgeBySim(sim);

  $('rvBody').innerHTML = `
    <div style="display:flex;gap:12px;margin-bottom:14px">
      ${assetImg(a) ? `<img src="${assetImg(a)}" style="width:104px;height:104px;object-fit:cover;border-radius:8px;border:1px solid var(--line);flex-shrink:0">` : ''}
      <div style="flex:1;min-width:0">
        <div style="font-size:13px;font-weight:700;margin-bottom:5px">NO-${String(a.asset_id).padStart(3, '0')}　${esc(a.title)}</div>
        <div style="font-size:11.5px;color:var(--ink3);margin-bottom:7px">权利人 ${esc(ownerName(a.owner_id))}　版本 v${a.version_no}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <span class="tag lv-undecided">待定</span>
          ${m ? `<span class="tag st-pending">${esc(m.motif_code)} ${esc(m.name)}</span>` : ''}
        </div>
      </div>
    </div>

    <div class="sec-t">AI 分析结果</div>
    <div class="vs" style="margin-bottom:10px">
      <div class="box ai">
        <div class="t">AI 建议</div>
        <div class="big">${LEVEL[a.ai_suggest_level]}</div>
        <div class="n">置信度 ${fmtPct(a.ai_confidence)}</div>
      </div>
      <div class="box human">
        <div class="t">按当前阈值</div>
        <div class="big">${LEVEL[byThr]}</div>
        <div class="n">阈值 ${S.thrD.toFixed(2)} / ${S.thrO.toFixed(2)}</div>
      </div>
    </div>
    ${sim ? simBar((m ? m.motif_code + ' ' + m.name : '最相似母题'), sim, sim >= S.thrO, S.thrD) : ''}

    <div class="sec-t">创作说明</div>
    <div style="font-size:12px;color:var(--ink2);line-height:1.8;text-align:justify">${esc(a.description || '—')}</div>

    <div class="sec-t">终审结论</div>
    <div class="lv-opts">
      ${[['original', '原创', '相似度低于下界，未依附现有母题'],
      ['derived', '改编衍生', '母题属公有，新增表达可主张权利'],
      ['public', '公有母题', '相似度过高，接近直接复制母题'],
      ['undecided', '未定', '证据不足，暂不确权']].map(([v, t, d]) => `
        <button class="lv-opt ${S.rvLevel === v ? 'on' : ''}" data-lv="${v}">
          <div class="t">${t}</div>
          <div class="d">${d}</div>
          ${v === a.ai_suggest_level ? '<span class="same">与 AI 同</span>' : ''}
        </button>`).join('')}
    </div>

    ${S.rvLevel && S.rvLevel !== a.ai_suggest_level ? `
      <div class="note-box acc" style="margin-top:11px">
        <b>这将是一次推翻</b>　AI 建议为「${LEVEL[a.ai_suggest_level]}」，你选择「${LEVEL[S.rvLevel]}」，
        提交后会写入 <code style="font-size:11px">is_override = true</code>，台账与溯源链中都可见此标记。
      </div>` : ''}

    <div class="sec-t">终审意见</div>
    <textarea class="inp" id="rvOpinion" placeholder="写明判定依据，例如：与 MO-001 相似度 0.8349，落在 [0.80, 0.92) 区间……">${esc(S.rvOpinion)}</textarea>

    <div class="sec-t">本次判定采用的阈值</div>
    <div class="rng">
      <input type="range" id="rvThr" min="50" max="95" value="${Math.round(S.thrD * 100)}">
      <span class="val" id="rvThrV">${S.thrD.toFixed(2)}</span>
    </div>
    <div style="font-size:11px;color:var(--ink3);margin-top:7px" id="rvThrHint">
      按 ${S.thrD.toFixed(2)} 判定：相似度 ${fmtSim(sim)} → ${LEVEL[byThr]}
    </div>

    <div style="display:flex;gap:9px;margin-top:18px">
      <button class="btn primary" id="btnApprove" style="flex:1" ${S.rvLevel ? '' : 'disabled'}>
        ${S.rvLevel ? '提交终审' : '请先选择结论档位'}
      </button>
      <button class="btn" onclick="openAssetDetail(${a.asset_id})">查看完整档案</button>
    </div>
  `;

  $('rvBody').querySelectorAll('[data-lv]').forEach(b => {
    b.onclick = () => {
      S.rvLevel = b.dataset.lv;
      S.rvOpinion = $('rvOpinion').value;
      renderReview();
    };
  });

  const ta = $('rvOpinion');
  if (ta) ta.oninput = () => { S.rvOpinion = ta.value; };

  const thr = $('rvThr');
  if (thr) thr.oninput = () => {
    S.thrD = Number(thr.value) / 100;
    $('rvThrV').textContent = S.thrD.toFixed(2);
    const nb = judgeBySim(sim);
    $('rvThrHint').innerHTML = `按 ${S.thrD.toFixed(2)} 判定：相似度 ${fmtSim(sim)} → <b style="color:var(--cin)">${LEVEL[nb]}</b>`;
  };

  const ap = $('btnApprove');
  if (ap) ap.onclick = () => {
    const opinion = ($('rvOpinion') && $('rvOpinion').value || '').trim();
    if (!opinion) { toast('请填写终审意见'); return; }
    const wasOverride = S.rvLevel !== a.ai_suggest_level;
    approveAsset(a, S.rvLevel, opinion);
    S.rvLevel = null; S.rvOpinion = '';
    // 自动选中下一件待审，连续处理时不用每次回左边点
    const rest = pendingList();
    S.rvPick = rest.length ? rest[0].asset_id : null;
    toast(wasOverride ? '已提交终审，本次结论推翻了 AI 建议' : '已提交终审，结论与 AI 建议一致');
    renderBadges();
    renderReview();
  };
}

/* 终审写入。真实系统里这一步是单事务 + 触发器 + 列级权限共同保证的，
   演示页只模拟了结果状态。 */
function approveAsset(a, level, opinion) {
  if (!a) return false;
  const rec = (a.audits || []).filter(x => x.similarity).slice(-1)[0] || {};
  a.right_level = level;
  a.audit_status = 'approved';
  a.decided_at = TODAY;
  a.audits = a.audits || [];
  a.audits.push({
    state: 'approved',
    ai_suggest_level: a.ai_suggest_level,
    ai_confidence: a.ai_confidence,
    top_motif_id: rec.top_motif_id || a.motif_id || null,
    similarity: rec.similarity || null,
    expert_level: level,
    expert_id: S.user.user_id,
    is_override: level !== a.ai_suggest_level,
    opinion: opinion,
    decided_at: TODAY,
  });
  return true;
}

/* ============================ 监管台账 ============================ */

function filteredLicenses() {
  let list = allLicenses();
  if (S.lType !== 'all') list = list.filter(l => l.license_type === S.lType);
  if (S.lScope === 'active') list = list.filter(l => !isExpired(l));
  if (S.lScope === 'expired') list = list.filter(l => isExpired(l));

  const by = {
    fee: (a, b) => Number(b.fee_cny || 0) - Number(a.fee_cny || 0),
    date: (a, b) => String(b.start_date || '').localeCompare(String(a.start_date || '')),
    asset: (a, b) => a.asset_id - b.asset_id,
  }[S.lSort];
  return list.slice().sort(by);
}

function renderLedger() {
  const all = allLicenses();
  const list = filteredLicenses();
  const total = list.reduce((s, l) => s + Number(l.fee_cny || 0), 0);
  const excl = list.filter(l => l.license_type === 'exclusive').length;
  const active = list.filter(l => !isExpired(l)).length;

  $('ledgerStats').innerHTML = [
    ['授权笔数', list.length, '笔', ''],
    ['授权金额', fmtMoney(total), '', 'acc'],
    ['独占授权', excl, '笔', 'in'],
    ['生效中', active, '笔', 'ok'],
  ].map(([k, v, u, c]) => `
    <div class="stat ${c}">
      <div class="k">${k}</div>
      <div class="v">${v}<small>${u}</small></div>
    </div>`).join('');

  $('lCount').textContent = `显示 ${list.length} / ${all.length} 笔`;

  $('lList').innerHTML = list.length ? `<table class="tt">
    <thead><tr>
      <th style="width:56px">编号</th>
      <th>作品</th>
      <th style="width:96px">权利人</th>
      <th>被授权方</th>
      <th style="width:92px">类型</th>
      <th style="width:96px">地域</th>
      <th style="width:172px">期限</th>
      <th style="width:90px">金额</th>
      <th style="width:70px">状态</th>
    </tr></thead>
    <tbody>${list.map(l => {
      const expired = isExpired(l);
      return `<tr>
        <td class="n">${String(l.asset_id).padStart(3, '0')}</td>
        <td class="nm">${esc(l.asset_title)}</td>
        <td>${esc(ownerName(l.owner_id))}</td>
        <td>${esc(l.licensee_name)}</td>
        <td><span class="tag ${l.license_type === 'exclusive' ? 'lv-derived' : 'lv-public'}">${LICENSE_TYPE[l.license_type] || l.license_type}</span></td>
        <td>${esc(l.territory || '—')}</td>
        <td class="n">${esc(l.start_date || '')} ~ ${esc(l.end_date || '')}</td>
        <td class="num">${fmtMoney(l.fee_cny)}</td>
        <td>${expired
          ? '<span class="tag st-draft">已到期</span>'
          : '<span class="tag st-approved">生效中</span>'}</td>
      </tr>`;
    }).join('')}</tbody>
    <tfoot><tr>
      <td colspan="7">合计 ${list.length} 笔</td>
      <td class="num">${fmtMoney(total)}</td>
      <td></td>
    </tr></tfoot>
  </table>` : `<div class="empty"><div class="ic">◌</div>没有符合条件的授权记录</div>`;
}

/* ============================ 溯源图谱 ============================ */

function renderTrace() {
  const list = visibleAssets().filter(a => a.audit_status !== 'draft');
  const sel = $('tPick');

  if (!sel.dataset.init) {
    sel.innerHTML = list.map(a =>
      `<option value="${a.asset_id}">NO-${String(a.asset_id).padStart(3, '0')}　${esc(a.title)}</option>`).join('');
    sel.dataset.init = '1';
    sel.onchange = () => { S.tPick = Number(sel.value); renderTrace(); };
    if (!S.tPick && list.length) S.tPick = list[0].asset_id;
  }
  if (!list.some(a => a.asset_id === S.tPick) && list.length) {
    S.tPick = list[0].asset_id;
    sel.value = String(S.tPick);
  }

  const a = list.find(x => x.asset_id === S.tPick);
  if (!a) {
    $('tGraph').innerHTML = `<div class="empty">暂无可溯源的已提交作品</div>`;
    $('tNodes').innerHTML = '';
    return;
  }

  const m = a.motif_id ? motifOf(a.motif_id) : null;
  const lastAudit = (a.audits || []).filter(x => x.state === 'approved').slice(-1)[0] || null;
  const lics = a.licenses || [];
  const rows = Math.max(1, lics.length);

  $('tSub').textContent = `${m ? '4' : '3'} 类节点　${lics.length} 条授权`;

  // 高度按授权条数增长；只留一行时不要留出多余空白
  const H = 118 + (rows - 1) * 74;
  const nodeW = 158, nodeH = 50;
  const x0 = 16, x1 = 200, x2 = 384, x3 = 568;
  const cy = 52;

  /* 节点两行：第一行左侧标题 + 右侧状态角标，第二行副标题独占整行。
     之前把角标和副标题都放在第二行，长名字会压在一起。 */
  const box = (x, y, color, title, sub, tag) => `
    <g>
      <rect x="${x}" y="${y}" width="${nodeW}" height="${nodeH}" rx="8"
            fill="#fff" stroke="${color}" stroke-width="1.4"/>
      <rect x="${x}" y="${y}" width="4" height="${nodeH}" rx="2" fill="${color}"/>
      <text x="${x + 14}" y="${y + 20}" font-size="12" font-weight="700" fill="#2b2622">${esc(title)}</text>
      ${tag ? `<text x="${x + nodeW - 9}" y="${y + 20}" font-size="9.5" fill="#b0a697" text-anchor="end">${esc(tag)}</text>` : ''}
      <text x="${x + 14}" y="${y + 36}" font-size="10.5" fill="#8a8178">${esc(sub)}</text>
    </g>`;

  const link = (xa, ya, xb, yb) =>
    `<path d="M${xa} ${ya} C${(xa + xb) / 2} ${ya}, ${(xa + xb) / 2} ${yb}, ${xb} ${yb}"
           fill="none" stroke="#cbbeae" stroke-width="1.3" marker-end="url(#arw)"/>`;

  let svg = `<svg class="trace-svg" viewBox="0 0 740 ${H}" width="100%" height="${H}">
    <defs>
      <marker id="arw" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
        <path d="M0 0 L8 4 L0 8 z" fill="#cbbeae"/>
      </marker>
    </defs>`;

  // 列标题
  [['母题来源', x0], ['作品登记', x1], ['专家终审', x2], ['授权流转', x3]].forEach(([t, x]) => {
    svg += `<text x="${x}" y="24" font-size="9.5" font-weight="700" fill="#b0a697" letter-spacing="1">${t}</text>`;
  });

  // 节点
  if (m) {
    svg += box(x0, cy, '#2f5d7c', m.motif_code + ' ' + m.name, shortTitle(m.ethnic + ' · ' + m.region, 11), '公有');
    svg += link(x0 + nodeW, cy + nodeH / 2, x1, cy + nodeH / 2);
  }
  svg += box(x1, cy, '#9d2f2a', 'NO-' + String(a.asset_id).padStart(3, '0'), shortTitle(a.title, 10), 'v' + a.version_no);
  svg += link(x1 + nodeW, cy + nodeH / 2, x2, cy + nodeH / 2);

  if (lastAudit) {
    svg += box(x2, cy, lastAudit.is_override ? '#9d2f2a' : '#2f6b4f',
      LEVEL[lastAudit.expert_level] || '已终审',
      shortTitle(ownerName(lastAudit.expert_id), 8) + ' 判定',
      lastAudit.is_override ? '推翻' : '一致');
    svg += link(x2 + nodeW, cy + nodeH / 2, x3, cy + nodeH / 2);
  } else {
    svg += `<rect x="${x2}" y="${cy}" width="${nodeW}" height="${nodeH}" rx="8"
      fill="#f2eee7" stroke="#e2dbd0" stroke-dasharray="4 3"/>
      <text x="${x2 + 14}" y="${cy + 29}" font-size="11" fill="#b0a697">尚未终审</text>`;
  }

  if (lics.length) {
    lics.forEach((l, i) => {
      const y = cy + i * 74;
      if (i > 0) svg += link(x3, cy + nodeH / 2, x3, y + nodeH / 2);
      svg += box(x3, y, '#a8661c', shortTitle(l.licensee_name, 7),
        LICENSE_TYPE[l.license_type] + ' · ' + fmtMoney(l.fee_cny),
        isExpired(l) ? '已到期' : '生效中');
    });
  } else {
    svg += `<rect x="${x3}" y="${cy}" width="${nodeW}" height="${nodeH}" rx="8"
      fill="#f2eee7" stroke="#e2dbd0" stroke-dasharray="4 3"/>
      <text x="${x3 + 14}" y="${cy + 29}" font-size="11" fill="#b0a697">暂无授权</text>`;
  }

  svg += `</svg>`;
  $('tGraph').innerHTML = svg;

  // 节点明细
  const nodes = [];
  if (m) nodes.push(['母题', m.motif_code + ' ' + m.name, `${m.ethnic}　${m.region}　${m.era}`, '公有领域，不可独占']);
  nodes.push(['作品', 'NO-' + String(a.asset_id).padStart(3, '0') + ' ' + a.title,
    `${ownerName(a.owner_id)}　v${a.version_no}`, `登记于 ${a.created_at || '—'}`]);
  if (lastAudit) nodes.push(['终审', LEVEL[lastAudit.expert_level] || '—',
    `${ownerName(lastAudit.expert_id)}　${lastAudit.decided_at || ''}`,
    lastAudit.is_override ? '推翻 AI 建议' : '与 AI 建议一致']);
  lics.forEach((l, i) => nodes.push([`授权 ${i + 1}`, l.licensee_name,
    `${LICENSE_TYPE[l.license_type]}　${l.territory || ''}`,
    `${l.start_date || ''} ~ ${l.end_date || ''}　${fmtMoney(l.fee_cny)}`]));

  $('tNodes').innerHTML = `<table class="tt" style="font-size:11.5px">
    <thead><tr><th style="width:70px">类型</th><th style="width:210px">节点</th><th>内容</th><th>备注</th></tr></thead>
    <tbody>${nodes.map(([t, n, c, r]) => `<tr>
      <td><span class="tag lv-public">${esc(t)}</span></td>
      <td class="nm">${esc(n)}</td>
      <td>${esc(c)}</td>
      <td style="color:var(--ink3)">${esc(r)}</td>
    </tr>`).join('')}</tbody>
  </table>`;
}

/* ============================ 审计日志 ============================ */

function renderAudit() {
  const types = [['all', '全部'], ['ai', 'AI 分析'], ['expert', '专家终审'], ['license', '授权'], ['system', '系统']];
  const box = $('auType');
  if (!box.dataset.init) {
    box.innerHTML = types.map(([v, t]) =>
      `<button data-v="${v}" class="${S.auType === v ? 'on' : ''}">${t}</button>`).join('');
    box.querySelectorAll('[data-v]').forEach(b => {
      b.onclick = () => {
        S.auType = b.dataset.v;
        box.querySelectorAll('[data-v]').forEach(x => x.classList.toggle('on', x === b));
        renderAudit();
      };
    });
    box.dataset.init = '1';
  }

  const ev = [];
  visibleAssets().forEach(a => {
    (a.audits || []).forEach(au => {
      ev.push({
        type: au.state === 'approved' ? 'expert' : 'ai',
        date: au.decided_at || au.created_at,
        title: au.state === 'approved' ? '专家终审' : '提交并完成 AI 分析',
        who: au.expert_id ? ownerName(au.expert_id) : '系统',
        ds: au.state === 'approved'
          ? `${a.title} → ${LEVEL[au.expert_level]}${au.is_override ? '（推翻 AI）' : ''}`
          : `${a.title} → AI 建议${LEVEL[au.ai_suggest_level]}，置信度 ${fmtPct(au.ai_confidence)}`,
        hi: !!au.is_override,
      });
    });
    (a.licenses || []).forEach(l => {
      ev.push({
        type: 'license',
        date: l.start_date,
        title: '授权登记',
        who: ownerName(a.owner_id),
        ds: `${a.title} → ${l.licensee_name}　${LICENSE_TYPE[l.license_type]}　${fmtMoney(l.fee_cny)}`,
        hi: false,
      });
    });
  });
  ev.push({ type: 'system', date: '2026-09-01', title: '系统初始化', who: 'admin01', ds: '导入 3 个公有母题，建立分级阈值 0.80 / 0.92', hi: false });

  const list = (S.auType === 'all' ? ev : ev.filter(e => e.type === S.auType))
    .sort((x, y) => String(y.date || '').localeCompare(String(x.date || '')));

  $('auList').innerHTML = list.length ? `<div class="tl">` + list.map(e => `
    <div class="tli ${e.hi ? 'hi' : ''}">
      <div class="tt">${esc(e.title)}
        <span class="tag lv-public" style="font-size:10px">${esc(e.who)}</span>
        <span class="tm">${esc(e.date || '')}</span>
        ${e.hi ? '<span class="tag override">推翻</span>' : ''}
      </div>
      <div class="ds">${esc(e.ds)}</div>
    </div>`).join('') + `</div>`
    : `<div class="empty"><div class="ic">◌</div>该类型下暂无记录</div>`;
}

/* ============================ 演示设置 ============================ */

function applyCfg() {
  document.body.classList.toggle('compact', !!S.cfg.compact);
  document.body.classList.toggle('no-anim', !S.cfg.anim);
  document.body.classList.toggle('no-hint', !S.cfg.showHint);
}

function renderSettings() {
  document.querySelectorAll('#setRow [data-cfg]').forEach(el => {
    el.classList.toggle('on', !!S.cfg[el.dataset.cfg]);
  });

  $('thrD').value = Math.round(S.thrD * 100);
  $('thrOv').value = Math.round(S.thrO * 100);
  $('thrDv').textContent = S.thrD.toFixed(2);
  $('thrOv').textContent = S.thrO.toFixed(2);
  $('thrNote').innerHTML =
    `当前规则：相似度 &lt; <b>${S.thrD.toFixed(2)}</b> 判原创；
     <b>${S.thrD.toFixed(2)} ~ ${S.thrO.toFixed(2)}</b> 判改编衍生；
     ≥ <b>${S.thrO.toFixed(2)}</b> 判公有母题。
     <br><br>真实系统中这两个阈值存放在配置表里，调整需审批并留审计记录；此处的拖动只影响本页演示计算。`;

  // 选项按可用页面动态生成，避免出现"值不存在导致下拉空白"
  const dp = $('defPage');
  if (!dp.dataset.init) {
    dp.innerHTML = Object.keys(PAGES)
      .filter(k => k !== 'settings')
      .map(k => `<option value="${k}">${PAGES[k].label}</option>`).join('');
    dp.dataset.init = '1';
  }
  dp.value = S.userPickPage || roleLanding();
  $('pageSize').value = String(S.pageSize);
}

/* ============================ 启动 ============================ */

function init() {
  /* ---- 登录 ---- */
  renderLoginAccounts();
  initSlider();
  syncSubmit();

  $('lgSubmit').onclick = doLogin;
  $('pw').addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });

  $('rememberSw').onclick = () => {
    S.remember = !S.remember;
    $('rememberSw').classList.toggle('on', S.remember);
  };

  $('forgot').onclick = e => {
    e.preventDefault();
    toast('演示环境：4 个账号密码均为 Passw0rd!');
  };

  /* ---- 导航 ---- */
  document.querySelectorAll('#nav button[data-pg]').forEach(b => {
    b.onclick = () => go(b.dataset.pg);
  });

  /* ---- 顶栏 ---- */
  $('meBox').onclick = e => {
    if (e.target.closest('.menu')) return;
    $('meMenu').classList.toggle('show');
  };
  document.addEventListener('click', e => {
    if (!e.target.closest('#meBox')) $('meMenu').classList.remove('show');
  });
  $('meMenu').querySelectorAll('.mi').forEach(b => {
    b.onclick = () => {
      const act = b.dataset.act;
      $('meMenu').classList.remove('show');
      if (act === 'logout') logout();
      else if (act === 'settings') go('settings');
      else toast('演示环境未提供该功能');
    };
  });

  $('btnBell').onclick = () => {
    const n = pendingList().length;
    if (!n) { toast('没有待办'); return; }
    if (PAGES.review.roles.includes(S.user.role)) { go('review'); toast(`${n} 件待审`); }
    else go('assets');
  };

  $('btnHelp').onclick = () => {
    openDrawer('这个演示怎么看', `
      <div class="sec-t">演示了什么</div>
      <p style="font-size:12.5px;color:var(--ink2);line-height:1.9">
        这是一份<b>纯前端</b>的可操作演示：数据是种子文件的副本，全部交互只改浏览器内存，
        刷新页面即恢复初始状态。它不对应任何真实数据库。
      </p>
      <div class="sec-t">四个值得点的地方</div>
      <div class="tl">
        <div class="tli hi"><div class="tt">换身份看权限差异</div>
          <div class="ds">退出登录后换成「传承人」，作品管理只剩自己的 3 件，且看不到台账与审计日志。</div></div>
        <div class="tli"><div class="tt">验证 AI 不改权属</div>
          <div class="ds">在上传分析页跑完一遍，回到作品管理看「专家结论」列仍是「未定」。</div></div>
        <div class="tli"><div class="tt">亲手推翻一次 AI</div>
          <div class="ds">专家工作台里选一件待审作品，把结论选成与 AI 不同的档位，提交后会打上「已推翻 AI」。</div></div>
        <div class="tli"><div class="tt">拖阈值看判定怎么变</div>
          <div class="ds">设置页拖动两个阈值滑块，回工作台看「按当前阈值」的结果随之变化。</div></div>
      </div>
      <div class="note-box warn" style="margin-top:14px">
        <b>边界说明</b>　列级权限、触发器、事务回滚、HNSW 索引这些要在真实 PostgreSQL 上才能验证，
        本页只能演示<b>界面行为与业务规则</b>。
      </div>
    `, `<button class="btn" onclick="closeDrawer()">知道了</button>`);
  };

  /* ---- 抽屉 ---- */
  $('dClose').onclick = closeDrawer;
  $('dMask').onclick = closeDrawer;
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });

  /* ---- 概览 ---- */
  $('dashRange').querySelectorAll('button').forEach(b => {
    b.onclick = () => {
      S.dashRange = b.dataset.v;
      $('dashRange').querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
      renderDashboard();
    };
  });

  /* ---- 作品管理 ---- */
  $('aQ').oninput = () => { S.aQ = $('aQ').value.trim(); renderAssets(); };

  $('aLv').querySelectorAll('button').forEach(b => {
    b.onclick = () => {
      S.aLv = b.dataset.v;
      $('aLv').querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
      renderAssets();
    };
  });

  $('aSt').onchange = () => { S.aSt = $('aSt').value; renderAssets(); };
  $('aSort').onchange = () => { S.aSort = $('aSort').value; renderAssets(); };

  $('aView').querySelectorAll('button').forEach(b => {
    b.onclick = () => {
      S.aView = b.dataset.v;
      $('aView').querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
      renderAssets();
    };
  });

  $('aBulk').querySelectorAll('[data-bulk]').forEach(b => {
    b.onclick = () => {
      const act = b.dataset.bulk;
      if (act === 'clear') { S.aSel.clear(); renderAssets(); return; }
      if (act === 'export') {
        toast(`已导出 ${S.aSel.size} 件作品的清单（演示环境仅提示）`);
      } else if (act === 'trace') {
        const first = [...S.aSel][0];
        if (first) { S.tPick = first; $('tPick').dataset.init = ''; go('trace'); }
      }
    };
  });

  $('btnExportAll').onclick = () => toast(`清单共 ${filteredAssets().length} 条（演示环境仅提示）`);
  $('btnGoUpload').onclick = () => go('upload');

  /* ---- 母题库 ---- */
  $('mQ').oninput = () => { S.mQ = $('mQ').value.trim(); renderMotifs(); };
  $('mCat').onchange = () => { S.mCat = $('mCat').value; renderMotifs(); };
  $('mEra').onchange = () => { S.mEra = $('mEra').value; renderMotifs(); };

  /* ---- 上传 ---- */
  $('btnAnalyze').onclick = runAnalyze;
  $('btnClearUp').onclick = () => {
    S.upPick = null; S.upResult = null;
    $('upResultTag').textContent = '';
    renderUpload();
  };
  $('upMotif').onchange = () => { S.upMotif = $('upMotif').value; };
  $('dropzone').onclick = () => toast('演示环境不读取本地文件，请从下方样例中选择');

  /* ---- 台账 ---- */
  $('lType').onchange = () => { S.lType = $('lType').value; renderLedger(); };
  $('lSort').onchange = () => { S.lSort = $('lSort').value; renderLedger(); };
  $('lScope').querySelectorAll('button').forEach(b => {
    b.onclick = () => {
      S.lScope = b.dataset.v;
      $('lScope').querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
      renderLedger();
    };
  });

  /* ---- 设置 ---- */
  document.querySelectorAll('#setRow [data-cfg]').forEach(el => {
    el.onclick = () => {
      const k = el.dataset.cfg;
      S.cfg[k] = !S.cfg[k];
      el.classList.toggle('on', S.cfg[k]);
      applyCfg();
      if (k === 'showConf') renderAssets();
      toast(k === 'showConf'
        ? (S.cfg.showConf ? '已显示 AI 置信度' : '已隐藏 AI 置信度')
        : (S.cfg[k] ? '已开启' : '已关闭'));
    };
  });

  $('thrD').oninput = () => {
    S.thrD = Number($('thrD').value) / 100;
    if (S.thrD >= S.thrO) { S.thrO = Math.min(0.99, S.thrD + 0.01); }
    renderSettings();
  };
  $('thrOv').oninput = () => {
    S.thrO = Number($('thrOv').value) / 100;
    if (S.thrO <= S.thrD) { S.thrD = Math.max(0.5, S.thrO - 0.01); }
    renderSettings();
  };

  $('defPage').onchange = () => {
    S.userPickPage = $('defPage').value;
    toast('已设为默认打开页：' + PAGES[S.userPickPage].label);
  };
  $('pageSize').onchange = () => { S.pageSize = Number($('pageSize').value); toast(`每页 ${S.pageSize} 条`); };

  $('btnResetAll').onclick = () => {
    ASSETS = JSON.parse(JSON.stringify(ASSETS_ORIGINAL));
    S.aSel.clear(); S.rvPick = null; S.rvLevel = null; S.rvOpinion = '';
    S.upPick = null; S.upResult = null;
    renderBadges();
    go(S.page);
    toast('演示数据已恢复初始状态');
  };

  /* ---- 初始筛选态 ---- */
  $('aLv').querySelector('[data-v="all"]').classList.add('on');
  $('aView').querySelector('[data-v="table"]').classList.add('on');
  $('lScope').querySelector('[data-v="all"]').classList.add('on');
  $('dashRange').querySelector('[data-v="month"]').classList.add('on');

  applyCfg();
}

init();
