#!/usr/bin/env node
/**
 * 前端静态校验：抽取 HTML 中的内联脚本与独立 JS 文件，做语法检查。
 * 用法：node _tools/check_frontend.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const FE = path.join(ROOT, '05_前端页面');

let errors = 0, checked = 0;

function checkCode(label, code) {
    checked++;
    try {
        new vm.Script(code, { filename: label });
        console.log('  [OK ] ' + label + '  (' + code.split('\n').length + ' 行)');
    } catch (e) {
        errors++;
        console.log('  [ERR] ' + label + '  => ' + e.message);
    }
}

// 1) 独立 JS 文件
['assets/js/api.js', 'assets/js/ui.js', 'assets/js/snapshot.js'].forEach(function (rel) {
    const p = path.join(FE, rel);
    if (!fs.existsSync(p)) { console.log('  [MISS] ' + rel); errors++; return; }
    const src = fs.readFileSync(p, 'utf8');
    if (rel.endsWith('snapshot.js')) {
        // 快照文件仅校验 JSON 部分是否可解析
        const m = src.match(/=\s*([\s\S]*);\s*$/);
        try { JSON.parse(m[1]); console.log('  [OK ] ' + rel + '  (JSON 合法)'); checked++; }
        catch (e) { errors++; console.log('  [ERR] ' + rel + ' JSON 解析失败 => ' + e.message); }
        return;
    }
    checkCode(rel, src);
});

// 2) HTML 内联脚本
fs.readdirSync(FE).filter(f => f.endsWith('.html')).forEach(function (f) {
    const html = fs.readFileSync(path.join(FE, f), 'utf8');
    const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)];
    if (!blocks.length) { console.log('  [--] ' + f + ' 无内联脚本'); return; }
    blocks.forEach(function (b, i) { checkCode(f + ' #inline' + (i + 1), b[1]); });
});

console.log('\n共检查 ' + checked + ' 段脚本，语法错误 ' + errors + ' 处。');
process.exit(errors ? 1 : 0);
