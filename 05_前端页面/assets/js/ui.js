/* ==========================================================================
   UI 组件层
   --------------------------------------------------------------------------
   包含：侧边栏渲染、Toast 提示、通用表格、抽屉、以及**不依赖任何外部库**
         的纯 SVG 图表（环图 / 横向条形图 / 纵向柱状图 / 折线图）。
   之所以手写 SVG 图表，是为了保证系统在完全离线（无 CDN）环境下仍可演示。
   ========================================================================== */
(function (global) {
    'use strict';

    var C = {
        ink: '#26221e', ink2: '#5c554d', ink3: '#93897d',
        line: '#e7e0d6', lineStrong: '#d6ccbe', bg: '#f6f3ee',
        red: '#a4342a', teal: '#1f5a63', gold: '#b0813a',
        green: '#2f7a55', violet: '#5b4b8a',
        series: ['#a4342a', '#1f5a63', '#b0813a', '#5b4b8a', '#2f7a55',
                 '#c9533f', '#4a8a94', '#cdab6c', '#8878b3', '#5aa07a']
    };

    var NAV = [
        { href: 'index.html', icon: '◈', text: '数据看板', desc: '全局 KPI 与可视化' },
        { href: 'projects.html', icon: '❖', text: '非遗档案', desc: '项目 / 传承人 / 作品' },
        { href: 'activities.html', icon: '◎', text: '研学活动', desc: '活动报名与智能推荐' },
        { href: 'queries.html', icon: '⌘', text: 'SQL 能力', desc: '数据库功能实况演示' }
    ];

    /* ---------------------------------------------------------- 侧边栏 */
    function renderSidebar(active) {
        var host = document.getElementById('sidebar');
        if (!host) return;
        var links = NAV.map(function (n) {
            return '<a href="' + n.href + '"' + (n.href === active ? ' class="active"' : '') + '>' +
                   '<span class="ico">' + n.icon + '</span><span>' + n.text + '</span></a>';
        }).join('');

        host.innerHTML =
            '<div class="brand">' +
              '<div class="brand-mark">遗</div>' +
              '<div class="brand-title">非遗数字档案平台</div>' +
              '<div class="brand-sub">地方非遗文化数字化保护<br>数据库管理系统</div>' +
            '</div>' +
            '<nav class="nav">' +
              '<div class="nav-group">功能导航</div>' + links +
            '</nav>' +
            '<div class="sidebar-foot" id="conn-state">' +
              '<span class="dot off"></span><span>正在探测后端服务…</span>' +
            '</div>';
    }

    function setConnState(online, msg) {
        var el = document.getElementById('conn-state');
        if (!el) return;
        var ch = (document.getElementById('topbar-conn') || {});
        var cls = online ? 'on' : 'off';
        var txt = online ? '后端服务在线' : '离线快照模式';
        el.innerHTML = '<span class="dot ' + cls + '"></span>' +
                       '<span>' + txt + '</span><br><span style="opacity:.75">' +
                       (msg || '') + '</span>';
        if (ch.className !== undefined) {
            ch.className = 'badge ' + (online ? 'green' : 'gold');
            ch.textContent = online ? '● 在线' : '● 离线演示';
        }
    }

    /* ---------------------------------------------------------- Toast */
    function toast(msg, type, ms) {
        var host = document.getElementById('toast-host');
        if (!host) {
            host = document.createElement('div');
            host.id = 'toast-host';
            document.body.appendChild(host);
        }
        var el = document.createElement('div');
        el.className = 'toast ' + (type || '');
        el.innerHTML = msg;
        host.appendChild(el);
        setTimeout(function () { el.style.opacity = '0'; el.style.transform = 'translateY(6px)'; }, (ms || 3800) - 260);
        setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, ms || 3800);
    }

    /* ---------------------------------------------------------- 工具 */
    function esc(s) {
        if (s === null || s === undefined) return '';
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function fmt(v, digits) {
        if (v === null || v === undefined || v === '') return '—';
        if (typeof v === 'number') {
            return digits === undefined ? v : Number(v).toFixed(digits);
        }
        return v;
    }

    function num(v) { var n = parseFloat(v); return isNaN(n) ? 0 : n; }

    /** 通用表格渲染
     *  @param columns [{key,label,align,width,render(row)}] */
    function table(host, columns, rows, opts) {
        opts = opts || {};
        if (!rows || !rows.length) {
            host.innerHTML = '<div class="empty">' + (opts.emptyText || '暂无数据') + '</div>';
            return;
        }
        var head = columns.map(function (c) {
            return '<th class="' + (c.align === 'right' ? 'num' : '') + '"' +
                   (c.width ? ' style="width:' + c.width + '"' : '') + '>' + esc(c.label) + '</th>';
        }).join('');

        var body = rows.map(function (r, i) {
            var tds = columns.map(function (c) {
                var v = c.render ? c.render(r, i) : esc(fmt(r[c.key]));
                return '<td class="' + (c.align === 'right' ? 'num' : '') +
                       (c.mono ? ' mono' : '') + '">' + v + '</td>';
            }).join('');
            return '<tr data-idx="' + i + '"' + (opts.onRow ? ' class="clickable"' : '') + '>' + tds + '</tr>';
        }).join('');

        host.innerHTML = '<div class="table-wrap"><table class="data">' +
                         '<thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div>';

        if (opts.onRow) {
            host.querySelectorAll('tbody tr').forEach(function (tr) {
                tr.addEventListener('click', function () { opts.onRow(rows[+tr.dataset.idx]); });
            });
        }
    }

    /* ---------------------------------------------------------- 图表 */
    function donut(host, data, opts) {
        opts = opts || {};
        var size = opts.size || 178, r = size / 2 - 14, cx = size / 2, cy = size / 2;
        var total = data.reduce(function (s, d) { return s + num(d.value); }, 0) || 1;
        var angle = -Math.PI / 2, rings = [];

        data.forEach(function (d, i) {
            var v = num(d.value);
            if (v <= 0) return;
            var a = v / total * Math.PI * 2, mid = angle + a / 2;
            var large = a > Math.PI ? 1 : 0;
            var x1 = cx + r * Math.cos(angle), y1 = cy + r * Math.sin(angle);
            var x2 = cx + r * Math.cos(angle + a), y2 = cy + r * Math.sin(angle + a);
            rings.push('<path d="M ' + cx + ' ' + cy + ' L ' + x1 + ' ' + y1 +
                       ' A ' + r + ' ' + r + ' 0 ' + large + ' 1 ' + x2 + ' ' + y2 + ' Z" fill="' +
                       (d.color || C.series[i % C.series.length]) + '" opacity=".92"/>');
            if (v / total > 0.07) {
                var lx = cx + r * 0.66 * Math.cos(mid), ly = cy + r * 0.66 * Math.sin(mid);
                rings.push('<text x="' + lx + '" y="' + (ly + 4) + '" text-anchor="middle" ' +
                           'font-size="11.5" font-weight="700" fill="#fff">' + v + '</text>');
            }
            angle += a;
        });

        var legend = data.map(function (d, i) {
            return '<span><i style="background:' + (d.color || C.series[i % C.series.length]) +
                   '"></i>' + esc(d.label) + ' <b style="color:' + C.ink + '">' + num(d.value) + '</b></span>';
        }).join('');

        host.innerHTML = '<div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap">' +
            '<svg class="chart" viewBox="0 0 ' + size + ' ' + size + '" style="width:' + size + 'px;flex:0 0 auto">' +
              rings.join('') +
              '<circle cx="' + cx + '" cy="' + cy + '" r="' + (r * 0.52) + '" fill="' + C.bg + '"/>' +
              '<text x="' + cx + '" y="' + (cy - 2) + '" text-anchor="middle" font-size="21" font-weight="700" fill="' +
                C.ink + '">' + total + '</text>' +
              '<text x="' + cx + '" y="' + (cy + 16) + '" text-anchor="middle" font-size="11" fill="' +
                C.ink3 + '">' + (opts.centerText || '合计') + '</text>' +
            '</svg>' +
            '<div class="legend" style="flex-direction:column;gap:8px">' + legend + '</div>' +
        '</div>';
    }

    function hbars(host, data, opts) {
        opts = opts || {};
        var rowH = opts.rowHeight || 30, labelW = opts.labelWidth || 104;
        var max = Math.max.apply(null, data.map(function (d) { return num(d.value); }).concat([1]));
        var height = data.length * rowH + 6;
        var w = 640, barArea = w - labelW - 62;

        var out = data.map(function (d, i) {
            var v = num(d.value), y = i * rowH;
            var bw = Math.max(2, v / max * barArea);
            var color = d.color || C.series[i % C.series.length];
            return '<text x="' + (labelW - 10) + '" y="' + (y + rowH / 2 + 4) + '" text-anchor="end" ' +
                     'font-size="12.5" fill="' + C.ink2 + '">' + esc(d.label) + '</text>' +
                   '<rect x="' + labelW + '" y="' + (y + 7) + '" width="' + barArea + '" height="12" ' +
                     'rx="6" fill="' + C.bg + '"/>' +
                   '<rect x="' + labelW + '" y="' + (y + 7) + '" width="' + bw + '" height="12" ' +
                     'rx="6" fill="' + color + '"/>' +
                   '<text x="' + (labelW + bw + 8) + '" y="' + (y + rowH / 2 + 4) + '" font-size="12.5" ' +
                     'font-weight="700" fill="' + C.ink + '">' + v + '</text>';
        }).join('');

        host.innerHTML = '<svg class="chart" viewBox="0 0 ' + w + ' ' + height + '" ' +
                         'style="height:' + height + 'px">' + out + '</svg>';
    }

    function columns(host, data, opts) {
        opts = opts || {};
        var w = 680, h = opts.height || 220, padL = 40, padB = 34, padT = 14;
        var plotW = w - padL - 16, plotH = h - padB - padT;
        var max = Math.max.apply(null, data.map(function (d) { return num(d.value); }).concat([1]));
        var step = plotW / Math.max(data.length, 1);
        var bw = Math.min(38, step * 0.55);

        var grid = '';
        for (var g = 0; g <= 4; g++) {
            var gy = padT + plotH - plotH * g / 4;
            grid += '<line x1="' + padL + '" y1="' + gy + '" x2="' + (w - 16) + '" y2="' + gy +
                    '" stroke="' + C.line + '" stroke-dasharray="3 4"/>' +
                    '<text x="' + (padL - 6) + '" y="' + (gy + 4) + '" text-anchor="end" font-size="10.5" fill="' +
                    C.ink3 + '">' + Math.round(max * g / 4) + '</text>';
        }

        var bars = data.map(function (d, i) {
            var v = num(d.value);
            var bh = Math.max(2, v / max * plotH);
            var x = padL + step * i + (step - bw) / 2;
            var y = padT + plotH - bh;
            return '<rect x="' + x + '" y="' + y + '" width="' + bw + '" height="' + bh + '" rx="5" ' +
                     'fill="' + (opts.color || C.teal) + '" opacity=".9"/>' +
                   '<text x="' + (x + bw / 2) + '" y="' + (y - 5) + '" text-anchor="middle" ' +
                     'font-size="11" font-weight="700" fill="' + C.ink + '">' + v + '</text>' +
                   '<text x="' + (x + bw / 2) + '" y="' + (h - 12) + '" text-anchor="middle" ' +
                     'font-size="10.5" fill="' + C.ink3 + '">' + esc(d.label) + '</text>';
        }).join('');

        host.innerHTML = '<svg class="chart" viewBox="0 0 ' + w + ' ' + h + '" style="height:' + h + 'px">' +
                         grid + bars + '</svg>';
    }

    function heatBars(host, data, opts) {
        opts = opts || {};
        var rowH = 30, labelW = 150;
        var max = Math.max.apply(null, data.map(function (d) { return num(d.score); }).concat([1]));
        var height = data.length * rowH + 6, w = 660, area = w - labelW - 74;

        var out = data.map(function (d, i) {
            var v = num(d.score), y = i * rowH;
            var bw = Math.max(3, v / max * area);
            var color = d.grade === 'S' ? C.red : d.grade === 'A' ? C.gold
                      : d.grade === 'B' ? C.teal : C.ink3;
            return '<text x="' + (labelW - 10) + '" y="' + (y + rowH / 2 + 4) + '" text-anchor="end" ' +
                     'font-size="12.5" fill="' + C.ink2 + '">' + (i + 1) + '. ' + esc(d.name) + '</text>' +
                   '<rect x="' + labelW + '" y="' + (y + 7) + '" width="' + area + '" height="12" rx="6" fill="' + C.bg + '"/>' +
                   '<rect x="' + labelW + '" y="' + (y + 7) + '" width="' + bw + '" height="12" rx="6" fill="' + color + '"/>' +
                   '<text x="' + (labelW + bw + 8) + '" y="' + (y + rowH / 2 + 4) + '" font-size="12" ' +
                     'font-weight="700" fill="' + color + '">' + v.toFixed(1) + '</text>';
        }).join('');

        host.innerHTML = '<svg class="chart" viewBox="0 0 ' + w + ' ' + height + '" style="height:' + height + 'px">' +
                         out + '</svg>';
    }

    /* ---------------------------------------------------------- 抽屉 */
    function drawer(title, sub, bodyHtml) {
        var mask = document.getElementById('drawer-mask');
        var panel = document.getElementById('drawer');
        if (!mask) {
            mask = document.createElement('div');
            mask.id = 'drawer-mask'; mask.className = 'drawer-mask';
            panel = document.createElement('aside');
            panel.id = 'drawer'; panel.className = 'drawer';
            document.body.appendChild(mask);
            document.body.appendChild(panel);
            mask.addEventListener('click', closeDrawer);
        }
        panel.innerHTML =
            '<div class="drawer-head">' +
              '<div><h2>' + title + '</h2><div class="sub">' + (sub || '') + '</div></div>' +
              '<button class="icon-btn" id="drawer-close" title="关闭">✕</button>' +
            '</div>' +
            '<div class="drawer-body">' + bodyHtml + '</div>';
        document.getElementById('drawer-close').addEventListener('click', closeDrawer);
        requestAnimationFrame(function () { mask.classList.add('show'); panel.classList.add('show'); });
        document.body.style.overflow = 'hidden';
    }

    function closeDrawer() {
        var mask = document.getElementById('drawer-mask'), panel = document.getElementById('drawer');
        if (mask) mask.classList.remove('show');
        if (panel) panel.classList.remove('show');
        document.body.style.overflow = '';
    }

    /* ---------------------------------------------------------- Tabs */
    function tabs(root) {
        var btns = root.querySelectorAll('.tabs button');
        btns.forEach(function (b) {
            b.addEventListener('click', function () {
                btns.forEach(function (x) { x.classList.remove('active'); });
                b.classList.add('active');
                root.querySelectorAll('.tab-panel').forEach(function (p) {
                    p.classList.toggle('active', p.dataset.tab === b.dataset.tab);
                });
            });
        });
        if (btns.length) btns[0].click();
    }

    global.HUI = {
        C: C, NAV: NAV,
        renderSidebar: renderSidebar, setConnState: setConnState,
        toast: toast, esc: esc, fmt: fmt, num: num, table: table,
        donut: donut, hbars: hbars, columns: columns, heatBars: heatBars,
        drawer: drawer, closeDrawer: closeDrawer, tabs: tabs
    };
})(window);
