/* =========================================================
   互动演示版 · 页面层（一）
   概览 / 作品管理 / 母题库
   ========================================================= */

/* ============================ 概览 ============================ */

function rangeStart() {
  const d = new Date('2026-10-30');
  if (S.dashRange === 'week') d.setDate(d.getDate() - 7);
  else if (S.dashRange === 'month') d.setDate(d.getDate() - 30);
  else return null;
  return d;
}

function renderDashboard() {
  const list = visibleAssets();
  const lic = allLicenses();
  const start = rangeStart();

  // 范围内新增的作品（按 created_at 过滤）
  const inRange = start
    ? list.filter(a => a.created_at && new Date(a.created_at) >= start)
    : list;

  const approved = list.filter(a => a.audit_status === 'approved').length;
  const pend = list.filter(a => a.audit_status === 'pending').length;
  const draft = list.filter(a => a.audit_status === 'draft').length;
  const fee = lic.reduce((s, l) => s + Number(l.fee_cny || 0), 0);

  $('dashSub').textContent = S.user.role === 'heritor'
    ? `名下 ${list.length} 件作品的资产与流转情况`
    : `当前身份可见 ${list.length} 件作品的资产与流转情况（${ROLE[S.user.role]}视角）`;

  $('dashStats').innerHTML = [
    ['作品总数', list.length, '件', '', `本期新增 ${inRange.length} 件`],
    ['已确权', approved, '件', 'ok', list.length ? `占比 ${Math.round(approved / list.length * 100)}%` : '—'],
    ['待审 / 草稿', pend + draft, '件', 'wa', `待审 ${pend}　草稿 ${draft}`],
    ['授权金额', fmtMoney(fee), '', 'acc', `共 ${lic.length} 笔授权`],
  ].map(([k, v, u, c, d]) => `
    <div class="stat ${c}">
      <div class="k">${k}</div>
      <div class="v">${v}<small>${u}</small></div>
      <div class="d">${d}</div>
    </div>`).join('');

  // 分级分布
  const counts = { original: 0, derived: 0, public: 0, undecided: 0 };
  list.forEach(a => { counts[a.right_level] = (counts[a.right_level] || 0) + 1; });
  const total = list.length || 1;
  const colors = { original: '#2f6b4f', derived: '#a8661c', public: '#2f5d7c', undecided: '#b0a697' };
  $('dashDist').innerHTML = Object.keys(counts).map(k => {
    const n = counts[k];
    const pct = Math.round(n / total * 100);
    return `<div class="bar">
      <span class="lb">${LEVEL[k]}</span>
      <span class="track">
        <span class="fill" style="width:${pct}%;background:${colors[k]}"></span>
      </span>
      <span class="pct">${n} 件</span>
    </div>
    <div style="font-size:10.5px;color:var(--ink4);margin:-4px 0 10px 83px">占 ${pct}%</div>`;
  }).join('');

  // 最近动态：把 audits 与 licenses 拼成事件流
  const feed = [];
  list.forEach(a => {
    (a.audits || []).forEach(au => {
      feed.push({
        d: au.decided_at || au.created_at,
        t: au.state === 'approved' ? '终审通过' : '提交审核',
        s: `${a.title}　${LEVEL[au.expert_level || au.ai_suggest_level]}${au.is_override ? '（推翻 AI）' : ''}`,
        hi: !!au.is_override,
      });
    });
    (a.licenses || []).forEach(l => {
      feed.push({
        d: l.start_date,
        t: '授权登记',
        s: `${a.title} → ${l.licensee_name}　${fmtMoney(l.fee_cny)}`,
        hi: false,
      });
    });
  });
  feed.sort((x, y) => String(y.d || '').localeCompare(String(x.d || '')));

  const feedShow = feed.slice(0, 6);
  $('dashFeed').innerHTML = feedShow.length ? `<div class="tl">` + feedShow.map(f => `
    <div class="tli ${f.hi ? 'hi' : ''}">
      <div class="tt">${esc(f.t)}<span class="tm">${esc(f.d || '')}</span></div>
      <div class="ds">${esc(f.s)}</div>
    </div>`).join('') + `</div>`
    : `<div class="empty"><div class="ic">◌</div>暂无动态</div>`;

  // 待办提醒
  const expired = lic.filter(isExpired).length;
  const todos = [];
  if (pend) todos.push([`${pend} 件作品待终审`, 'review', '进入专家工作台处理']);
  if (draft) todos.push([`${draft} 件草稿未提交`, 'assets', '补充材料后提交审核']);
  if (expired) todos.push([`${expired} 笔授权已到期`, 'ledger', '核对是否需要续签']);
  if (!todos.length) todos.push(['暂无待办事项', 'dashboard', '当前处理完毕']);

  $('dashTodo').innerHTML = todos.map(([t, pg, d]) => {
    const can = PAGES[pg].roles.includes(S.user.role);
    return `<div style="display:flex;align-items:center;gap:11px;padding:9px 0;border-bottom:1px dashed var(--line2)">
      <span style="width:5px;height:5px;border-radius:50%;background:var(--cin);flex-shrink:0"></span>
      <div style="flex:1">
        <div style="font-size:12.5px;font-weight:700">${esc(t)}</div>
        <div style="font-size:11px;color:var(--ink3)">${esc(d)}</div>
      </div>
      ${can ? `<button class="btn sm" data-goto="${pg}">前往</button>` : ''}
    </div>`;
  }).join('');

  document.querySelectorAll('#dashTodo [data-goto]').forEach(b => {
    b.onclick = () => go(b.dataset.goto);
  });
}

/* ============================ 作品管理 ============================ */

function filteredAssets() {
  let list = visibleAssets();

  if (S.aQ) {
    const q = S.aQ.toLowerCase();
    list = list.filter(a =>
      a.title.toLowerCase().includes(q) ||
      String(a.asset_id).includes(q) ||
      ('NO-' + String(a.asset_id).padStart(3, '0')).toLowerCase().includes(q));
  }
  if (S.aLv !== 'all') list = list.filter(a => a.right_level === S.aLv);
  if (S.aSt !== 'all') list = list.filter(a => a.audit_status === S.aSt);

  const by = {
    id: (a, b) => a.asset_id - b.asset_id,
    date: (a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')),
    name: (a, b) => a.title.localeCompare(b.title, 'zh'),
    lic: (a, b) => (b.licenses || []).length - (a.licenses || []).length,
  }[S.aSort];
  return list.slice().sort(by);
}

function renderAssets() {
  const list = filteredAssets();
  const all = visibleAssets();

  $('assetsSub').textContent = S.user.role === 'heritor'
    ? `名下 ${all.length} 件作品　AI 建议与专家结论分列，权属以专家结论为准`
    : `全部 ${all.length} 件作品　当前以${ROLE[S.user.role]}身份查看`;

  // 批量条
  const bulk = $('aBulk');
  if (S.aSel.size) {
    bulk.style.display = 'flex';
    $('aBulkCnt').textContent = `已选 ${S.aSel.size} 项`;
  } else {
    bulk.style.display = 'none';
  }

  const box = $('aList');

  if (!list.length) {
    box.innerHTML = `<div class="empty"><div class="ic">◌</div>没有符合条件的作品，试着放宽筛选条件</div>`;
    return;
  }

  if (S.aView === 'card') {
    box.innerHTML = `<div class="card-b"><div class="mgrid">` + list.map(a => `
      <div class="mcard" data-aid="${a.asset_id}">
        <div class="img">
          ${assetImg(a) ? `<img src="${assetImg(a)}" alt="">` : ''}
          <span class="code">NO-${String(a.asset_id).padStart(3, '0')}</span>
        </div>
        <div class="in">
          <h4>${esc(a.title)}</h4>
          <div class="meta">
            ${lvTag(a.right_level)}
            ${stTag(a.audit_status)}
          </div>
          <div class="cnt">
            版本 v${a.version_no}　授权 ${(a.licenses || []).length} 笔　${esc(a.created_at || '')}
          </div>
        </div>
      </div>`).join('') + `</div></div>`;
    box.querySelectorAll('.mcard').forEach(el => {
      el.onclick = () => openAssetDetail(Number(el.dataset.aid));
    });
    return;
  }

  // 表格视图
  const allChecked = list.every(a => S.aSel.has(a.asset_id));
  box.innerHTML = `<div class="tw"><table class="tt">
    <thead><tr>
      <th style="width:34px"><input type="checkbox" class="ck" id="aAll" ${allChecked ? 'checked' : ''}></th>
      <th style="width:52px">编号</th>
      <th style="width:40px"></th>
      <th>作品名称</th>
      <th style="width:96px">母题</th>
      <th style="width:112px">AI 建议</th>
      <th style="width:96px">专家结论</th>
      <th style="width:76px">状态</th>
      <th style="width:48px">版本</th>
      <th style="width:96px">创建时间</th>
    </tr></thead>
    <tbody>` + list.map(a => `
      <tr class="clickable ${S.aSel.has(a.asset_id) ? 'sel' : ''}" data-aid="${a.asset_id}">
        <td><input type="checkbox" class="ck" data-ck="${a.asset_id}" ${S.aSel.has(a.asset_id) ? 'checked' : ''}></td>
        <td class="n">${String(a.asset_id).padStart(3, '0')}</td>
        <td>${assetImg(a) ? `<img class="thumb" src="${assetImg(a)}" alt="">` : ''}</td>
        <td class="nm">${esc(a.title)}</td>
        <td>${a.motif_id ? `<span style="font-size:11.5px">${esc((motifOf(a.motif_id) || {}).motif_code || '')}</span>` : '<span style="color:var(--ink4)">—</span>'}</td>
        <td>
          ${lvTag(a.ai_suggest_level)}
          ${S.cfg.showConf && a.ai_confidence > 0
            ? `<span style="font-size:10.5px;color:var(--ink4);margin-left:4px">${fmtPct(a.ai_confidence)}</span>`
            : ''}
        </td>
        <td>${a.audit_status === 'approved' ? lvTag(a.right_level) : '<span style="color:var(--ink4);font-size:11px">未定</span>'}</td>
        <td>${stTag(a.audit_status)}</td>
        <td class="n">v${a.version_no}</td>
        <td class="n">${esc(a.created_at || '')}</td>
      </tr>`).join('') + `</tbody>
    <tfoot><tr>
      <td colspan="4">共 ${list.length} 件${S.aSel.size ? `（已选 ${S.aSel.size}）` : ''}</td>
      <td colspan="3">已确权 ${list.filter(a => a.audit_status === 'approved').length} 件</td>
      <td colspan="3">授权合计 ${fmtMoney(list.reduce((s, a) => s + (a.licenses || []).reduce((x, l) => x + Number(l.fee_cny || 0), 0), 0))}</td>
    </tr></tfoot>
  </table></div>`;

  // 全选
  const allBox = $('aAll');
  if (allBox) allBox.onchange = () => {
    if (allBox.checked) list.forEach(a => S.aSel.add(a.asset_id));
    else list.forEach(a => S.aSel.delete(a.asset_id));
    renderAssets();
  };

  // 行内 checkbox：阻止冒泡，避免同时打开详情
  box.querySelectorAll('[data-ck]').forEach(ck => {
    ck.onclick = e => {
      e.stopPropagation();
      const id = Number(ck.dataset.ck);
      if (ck.checked) S.aSel.add(id); else S.aSel.delete(id);
      renderAssets();
    };
  });

  // 行点击 → 详情
  box.querySelectorAll('tr[data-aid]').forEach(tr => {
    tr.onclick = () => openAssetDetail(Number(tr.dataset.aid));
  });
}

/* 作品详情抽屉：AI 与专家并列，避免把两者混为一谈 */
function openAssetDetail(aid) {
  const a = ASSETS.find(x => x.asset_id === aid);
  if (!a) return;
  const m = a.motif_id ? motifOf(a.motif_id) : null;
  const lastAudit = (a.audits || []).filter(x => x.state === 'approved').slice(-1)[0] || null;

  const html = `
    ${assetImg(a) ? `<img src="${assetImg(a)}" style="width:100%;max-height:200px;object-fit:cover;border-radius:9px;border:1px solid var(--line);margin-bottom:14px">` : ''}

    <div class="sec-t">基本信息</div>
    <dl class="kv">
      <dt>编号</dt><dd>NO-${String(a.asset_id).padStart(3, '0')}</dd>
      <dt>名称</dt><dd>${esc(a.title)}</dd>
      <dt>权利人</dt><dd>${esc(ownerName(a.owner_id))}</dd>
      <dt>关联母题</dt><dd>${m ? esc(m.motif_code + '　' + m.name) : '<span style="color:var(--ink4);font-weight:400">无（原创作品）</span>'}</dd>
      <dt>当前权属</dt><dd>${lvTag(a.right_level)}</dd>
      <dt>审核状态</dt><dd>${stTag(a.audit_status)}</dd>
      <dt>版本</dt><dd>v${a.version_no}</dd>
      <dt>创建时间</dt><dd>${esc(a.created_at || '—')}</dd>
    </dl>

    <div class="sec-t">创作说明</div>
    <div style="font-size:12.5px;color:var(--ink2);line-height:1.8;text-align:justify">${esc(a.description || '—')}</div>

    <div class="sec-t">AI 建议与专家结论</div>
    <div class="vs">
      <div class="box ai">
        <div class="t">AI 建议（仅供参考）</div>
        <div class="big">${LEVEL[a.ai_suggest_level] || '—'}</div>
        <div class="n">置信度 ${fmtPct(a.ai_confidence)}</div>
        ${lastAudit && lastAudit.similarity ? `<div class="n">最高相似度 ${fmtSim(lastAudit.similarity)}</div>` : ''}
      </div>
      <div class="box human">
        <div class="t">专家结论（已生效）</div>
        <div class="big">${a.audit_status === 'approved' ? (LEVEL[a.right_level] || '—') : '未定'}</div>
        <div class="n">${a.audit_status === 'approved'
          ? (lastAudit && lastAudit.is_override ? '<span class="tag override">已推翻 AI</span>' : '与 AI 一致')
          : '尚待终审'}</div>
      </div>
    </div>

    ${lastAudit && lastAudit.opinion ? `
      <div class="note-box" style="margin-top:12px">
        <b>终审意见</b><br>${esc(lastAudit.opinion)}
      </div>` : ''}

    ${m && lastAudit && lastAudit.similarity ? `
      <div class="sec-t">相似度对照</div>
      ${simBar(m.motif_code + ' ' + m.name, lastAudit.similarity, lastAudit.similarity >= S.thrO, S.thrD)}
      <div style="font-size:10.5px;color:var(--ink4);margin-top:4px">
        竖线为「原创 / 改编」分界阈值 ${S.thrD.toFixed(2)}
      </div>` : ''}

    <div class="sec-t">溯源链</div>
    <div class="tl">
      ${m ? `<div class="tli"><div class="tt">母题来源<span class="tm">${esc(m.region)}</span></div>
        <div class="ds">${esc(m.motif_code)} ${esc(m.name)}，${esc(m.ethnic)}，${esc(m.era)}，属公有领域</div></div>` : ''}
      <div class="tli"><div class="tt">作品登记<span class="tm">${esc(a.created_at || '')}</span></div>
        <div class="ds">权利人 ${esc(ownerName(a.owner_id))} 提交，版本 v${a.version_no}</div></div>
      ${(a.audits || []).map(au => `
        <div class="tli ${au.is_override ? 'hi' : ''}">
          <div class="tt">${au.state === 'approved' ? '专家终审' : '提交审核'}
            <span class="tm">${esc(au.decided_at || au.created_at || '')}</span>
            ${au.is_override ? '<span class="tag override">推翻 AI</span>' : ''}</div>
          <div class="ds">${au.expert_level
            ? `判定为${LEVEL[au.expert_level]}，终审人 ${esc(ownerName(au.expert_id))}`
            : `AI 建议${LEVEL[au.ai_suggest_level]}，等待专家${au.similarity ? `，相似度 ${fmtSim(au.similarity)}` : ''}`}</div>
        </div>`).join('')}
      ${(a.licenses || []).map(l => `
        <div class="tli"><div class="tt">授权登记<span class="tm">${esc(l.start_date || '')}</span></div>
          <div class="ds">${esc(LICENSE_TYPE[l.license_type])}予 ${esc(l.licensee_name)}，${esc(l.scope)}，${fmtMoney(l.fee_cny)}</div></div>`).join('')}
    </div>

    <div class="sec-t">授权记录</div>
    ${(a.licenses || []).length ? `<table class="tt" style="font-size:11.5px">
      <thead><tr><th>被授权方</th><th>类型</th><th>金额</th><th>期限</th></tr></thead>
      <tbody>${a.licenses.map(l => `<tr>
        <td>${esc(l.licensee_name)}</td>
        <td>${esc(LICENSE_TYPE[l.license_type])}</td>
        <td class="num">${fmtMoney(l.fee_cny)}</td>
        <td class="n">${esc(l.start_date || '')} ~ ${esc(l.end_date || '')}</td>
      </tr>`).join('')}</tbody>
    </table>` : `<div style="font-size:12px;color:var(--ink4)">暂无授权记录</div>`}
  `;

  openDrawer(`NO-${String(a.asset_id).padStart(3, '0')}　${a.title}`, html,
    `<button class="btn" onclick="closeDrawer()">关闭</button>
     ${a.audit_status === 'pending' && PAGES.review.roles.includes(S.user.role)
      ? `<button class="btn primary" data-review="${a.asset_id}">去终审这件</button>` : ''}`);

  document.querySelectorAll('#dFoot [data-review]').forEach(b => {
    b.onclick = () => {
      closeDrawer();
      S.rvPick = Number(b.dataset.review);
      go('review');
    };
  });
}

/* ============================ 母题库 ============================ */

function renderMotifs() {
  // 筛选项只初始化一次
  const catSel = $('mCat'), eraSel = $('mEra');
  if (catSel.options.length <= 1) {
    [...new Set(MOTIFS.map(m => m.category))].forEach(c => {
      catSel.insertAdjacentHTML('beforeend', `<option value="${esc(c)}">${esc(c)}</option>`);
    });
    [...new Set(MOTIFS.map(m => m.era))].forEach(e => {
      eraSel.insertAdjacentHTML('beforeend', `<option value="${esc(e)}">${esc(e)}</option>`);
    });
  }

  const ethBox = $('mEthnic');
  if (!ethBox.dataset.init) {
    const eths = [...new Set(MOTIFS.map(m => m.ethnic))];
    ethBox.innerHTML = eths.map(e =>
      `<button data-eth="${esc(e)}">${esc(e)}</button>`).join('');
    ethBox.querySelectorAll('[data-eth]').forEach(b => {
      b.onclick = () => {
        const e = b.dataset.eth;
        if (S.mEthnic.has(e)) S.mEthnic.delete(e); else S.mEthnic.add(e);
        b.classList.toggle('on', S.mEthnic.has(e));
        renderMotifs();
      };
    });
    ethBox.dataset.init = '1';
  }

  let list = MOTIFS.slice();
  if (S.mQ) {
    const q = S.mQ.toLowerCase();
    list = list.filter(m =>
      m.name.toLowerCase().includes(q) ||
      m.motif_code.toLowerCase().includes(q) ||
      (m.keywords || []).some(k => k.toLowerCase().includes(q)));
  }
  if (S.mEthnic.size) list = list.filter(m => S.mEthnic.has(m.ethnic));
  if (S.mCat !== 'all') list = list.filter(m => m.category === S.mCat);
  if (S.mEra !== 'all') list = list.filter(m => m.era === S.mEra);

  $('mCount').textContent = `显示 ${list.length} / ${MOTIFS.length} 个母题`;

  $('mList').innerHTML = list.length ? list.map(m => {
    const linked = ASSETS.filter(a => a.motif_id === m.motif_id);
    return `<div class="mcard" data-mid="${m.motif_id}">
      <div class="img">
        ${motifImg(m) ? `<img src="${motifImg(m)}" alt="">` : ''}
        <span class="code">${esc(m.motif_code)}</span>
      </div>
      <div class="in">
        <h4>${esc(m.name)}</h4>
        <div class="meta">
          <span>${esc(m.ethnic)}</span>
          <span>${esc(m.region)}</span>
        </div>
        <div class="meta"><span>${esc(m.category)}</span></div>
        <div class="kw">${(m.keywords || []).map(k => `<span>${esc(k)}</span>`).join('')}</div>
        <div class="cnt">关联作品 ${linked.length} 件</div>
      </div>
    </div>`;
  }).join('') : `<div class="empty"><div class="ic">◌</div>没有符合条件的母题</div>`;

  document.querySelectorAll('#mList .mcard').forEach(el => {
    el.onclick = () => openMotifDetail(Number(el.dataset.mid));
  });
}

function openMotifDetail(mid) {
  const m = MOTIFS.find(x => x.motif_id === mid);
  if (!m) return;
  const linked = ASSETS.filter(a => a.motif_id === m.motif_id);

  const html = `
    ${motifImg(m) ? `<img src="${motifImg(m)}" style="width:100%;max-height:220px;object-fit:cover;border-radius:9px;border:1px solid var(--line);margin-bottom:14px">` : ''}
    <dl class="kv">
      <dt>母题编号</dt><dd>${esc(m.motif_code)}</dd>
      <dt>名称</dt><dd>${esc(m.name)}</dd>
      <dt>民族</dt><dd>${esc(m.ethnic)}</dd>
      <dt>地域</dt><dd>${esc(m.region)}</dd>
      <dt>类别</dt><dd>${esc(m.category)}</dd>
      <dt>年代</dt><dd>${esc(m.era)}</dd>
    </dl>

    <div class="sec-t">说明</div>
    <div style="font-size:12.5px;color:var(--ink2);line-height:1.8;text-align:justify">${esc(m.description)}</div>

    <div class="note-box acc" style="margin-top:14px">
      <b>公有属性</b>　该母题属公有领域传统纹样，任何个人不得独占。
      创作者可对<b>在此基础上新增的独创表达</b>主张权利，但不能就母题本身体主张权利。
    </div>

    <div class="sec-t">关键词</div>
    <div class="chips" style="pointer-events:none">
      ${(m.keywords || []).map(k => `<button class="on">${esc(k)}</button>`).join('')}
    </div>

    <div class="sec-t">关联作品（${linked.length} 件）</div>
    ${linked.length ? linked.map(a => `
      <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px dashed var(--line2)">
        ${assetImg(a) ? `<img src="${assetImg(a)}" style="width:32px;height:32px;border-radius:5px;object-fit:cover">` : ''}
        <div style="flex:1">
          <div style="font-size:12px;font-weight:600">${esc(a.title)}</div>
          <div style="font-size:10.5px;color:var(--ink3)">${esc(ownerName(a.owner_id))}　v${a.version_no}</div>
        </div>
        ${lvTag(a.right_level)}
      </div>`).join('') : `<div style="font-size:12px;color:var(--ink4)">暂无关联作品</div>`}
  `;

  openDrawer(`${m.motif_code}　${m.name}`, html,
    `<button class="btn" onclick="closeDrawer()">关闭</button>`);
}
