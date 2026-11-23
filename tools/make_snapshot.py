#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
离线快照生成器
--------------------------------------------------------------------------
用途：把后端 REST 接口的真实返回值导出为前端可直接加载的 JS 文件
      （05_前端页面/assets/js/snapshot.js），使前端在**不启动后端**的情况下
      也能完整演示（答辩时若数据库环境不可用可作为降级方案）。

原理：直接使用 Flask 的 test_client，等价于对真实接口发请求，
      因此快照内容与接口输出 100% 一致。
"""

import io
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
sys.path.insert(0, os.path.join(ROOT, '03_后端服务'))

import app as backend  # noqa: E402

OUT = os.path.join(ROOT, '05_前端页面', 'assets', 'js', 'snapshot.js')


def main():
    client = backend.app.test_client()
    snap = {}

    def grab(key, url):
        resp = client.get(url)
        payload = resp.get_json()
        snap[key] = payload.get('data') if payload and payload.get('success') else None
        print('  %-26s %-52s -> %s' % (key, url, 'OK' if snap[key] is not None else 'FAIL'))

    print('采集接口数据 ...')
    grab('overview', '/api/overview')
    grab('enums', '/api/meta/enums')
    grab('categories', '/api/categories')
    grab('projects', '/api/projects?size=200&sort=heat')
    grab('projectHeat', '/api/projects/heat?top=50')
    grab('inheritors', '/api/inheritors?size=200')
    grab('works', '/api/works?size=500')
    grab('courses', '/api/courses')
    grab('activities', '/api/activities?size=200')
    grab('students', '/api/students')
    grab('reportCategory', '/api/reports/category')
    grab('reportDigitization', '/api/reports/digitization')
    grab('reportActivity', '/api/reports/activity')
    grab('logs', '/api/logs?limit=50')
    grab('demoQueries', '/api/demo/queries')

    snap['projectDetail'] = {}
    for pid in range(1, 13):
        resp = client.get('/api/projects/%d' % pid)
        p = resp.get_json()
        if p and p.get('success'):
            snap['projectDetail'][str(pid)] = p['data']
    print('  projectDetail            12 个项目完整档案 -> OK')

    snap['recommend'] = {}
    for sid in range(1, 17):
        resp = client.get('/api/algorithms/recommend?student_id=%d&top=6' % sid)
        p = resp.get_json()
        if p and p.get('success'):
            snap['recommend'][str(sid)] = p['data']
    print('  recommend                16 名学生的推荐结果 -> OK')

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    text = ('/* 自动生成：由 _tools/make_snapshot.py 从后端真实接口导出，请勿手工修改 */\n'
            'window.HERITAGE_SNAPSHOT = ' + json.dumps(snap, ensure_ascii=False, indent=1) + ';\n')
    with io.open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(text)

    print('\n快照已写入: %s (%.1f KB)' % (OUT, len(text.encode('utf-8')) / 1024.0))


if __name__ == '__main__':
    main()
