/* ==========================================================================
   API 数据层
   --------------------------------------------------------------------------
   设计要点：
   1. 前后端分离：页面通过 REST 接口获取数据（后端 Flask 服务）；
   2. 优雅降级：若后端未启动（例如直接用 file:// 打开页面），
      自动切换到 assets/js/snapshot.js 中的离线快照，
      保证演示环境无关，答辩时不依赖数据库在线。
   3. 统一出口：所有页面只调用 HDATA.get(key)，无需关心在线/离线差异。
   ========================================================================== */
(function (global) {
    'use strict';

    var SNAP = global.HERITAGE_SNAPSHOT || {};
    var BASE = (location.protocol === 'http:' || location.protocol === 'https:')
        ? '' : 'http://127.0.0.1:5000';

    var state = { online: null, message: '' };

    /* ---------------------------------------------------- 底层请求 */
    function withTimeout(promise, ms) {
        return new Promise(function (resolve, reject) {
            var t = setTimeout(function () { reject(new Error('timeout')); }, ms);
            promise.then(function (v) { clearTimeout(t); resolve(v); },
                         function (e) { clearTimeout(t); reject(e); });
        });
    }

    function request(path, options, ms) {
        return withTimeout(fetch(BASE + path, options), ms || 6000).then(function (r) {
            return r.json();
        }).then(function (body) {
            if (body && body.success === false) throw new Error(body.message || '接口返回失败');
            return body ? body.data : null;
        });
    }

    /* ---------------------------------------------------- 快照读取 */
    function snapGet(path) {
        var parts = String(path).split('.'), cur = SNAP;
        for (var i = 0; i < parts.length; i++) {
            if (cur == null) return null;
            cur = cur[parts[i]];
        }
        return cur === undefined ? null : cur;
    }

    /* 接口路径 -> 快照键 的映射表 */
    var MAP = {
        '/api/overview': 'overview',
        '/api/meta/enums': 'enums',
        '/api/categories': 'categories',
        '/api/projects': 'projects',
        '/api/projects/heat': 'projectHeat',
        '/api/inheritors': 'inheritors',
        '/api/works': 'works',
        '/api/courses': 'courses',
        '/api/activities': 'activities',
        '/api/students': 'students',
        '/api/reports/category': 'reportCategory',
        '/api/reports/digitization': 'reportDigitization',
        '/api/reports/activity': 'reportActivity',
        '/api/logs': 'logs',
        '/api/demo/queries': 'demoQueries',
        '/api/algorithms/heat': 'projectHeat'
    };

    /* ---------------------------------------------------- 在线探测 */
    function probe() {
        if (state.online !== null) return Promise.resolve(state.online);
        return withTimeout(fetch(BASE + '/api/health'), 1500)
            .then(function (r) { state.online = r.ok; })
            .catch(function () { state.online = false; })
            .then(function () {
                state.message = state.online
                    ? '已连接后端 REST 服务与数据库'
                    : '后端未启动，已切换到离线快照演示模式';
                return state.online;
            });
    }

    /* ---------------------------------------------------- 对外接口 */
    var HDATA = {
        get online() { return state.online; },
        get message() { return state.message; },
        get base() { return BASE; },

        probe: probe,

        /** 读取一个数据集：在线走接口，离线读快照 */
        get: function (path, params) {
            var url = buildUrl(path, params);
            var key = MAP[path];
            return probe().then(function (isOnline) {
                if (!isOnline) return snapGet(key);
                return request(url).catch(function (e) {
                    console.warn('[API] ' + url + ' 失败，降级快照：' + e.message);
                    return snapGet(key);
                });
            });
        },

        /** 项目完整档案（存储过程多结果集） */
        projectDetail: function (pid) {
            return probe().then(function (isOnline) {
                if (!isOnline) return snapGet('projectDetail.' + pid);
                return request('/api/projects/' + pid)
                    .catch(function () { return snapGet('projectDetail.' + pid); });
            });
        },

        /** 活动详情 + 报名名单 */
        activityDetail: function (aid) {
            return probe().then(function (isOnline) {
                if (!isOnline) return null;
                return request('/api/activities/' + aid).catch(function () { return null; });
            });
        },

        /** 推荐算法 */
        recommend: function (sid, top) {
            top = top || 6;
            return probe().then(function (isOnline) {
                if (!isOnline) return snapGet('recommend.' + sid);
                return request('/api/algorithms/recommend?student_id=' + sid + '&top=' + top)
                    .catch(function () { return snapGet('recommend.' + sid); });
            });
        },

        /** 学生报名（写操作，必须在线） */
        register: function (studentId, activityId) {
            return probe().then(function (isOnline) {
                if (!isOnline) {
                    throw new Error('离线演示模式下无法写入数据库，请先启动后端服务（python app.py）');
                }
                return request('/api/registrations', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ student_id: studentId, activity_id: activityId })
                }, 10000);
            });
        }
    };

    function buildUrl(path, params) {
        if (!params) return path;
        var pairs = [];
        Object.keys(params).forEach(function (k) {
            var v = params[k];
            if (v !== '' && v !== null && v !== undefined) {
                pairs.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
            }
        });
        return pairs.length ? path + '?' + pairs.join('&') : path;
    }

    global.HDATA = HDATA;
})(window);
