#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定向修正 make_report.py 中中文引号开闭错位的问题片段。"""

import io
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(BASE, 'make_report.py')

PAIRS = [
    ("'（项目-传承人表以\u201d项目ID+传承人ID\u201c为唯一键，报名表以\u201d活动ID+学生ID\u201c'",
     "'（项目-传承人表以\u201c项目ID+传承人ID\u201d为唯一键，报名表以\u201c活动ID+学生ID\u201d'"),
    ("'是否主传承人这三个属性必须同时知道\u201d是哪个项目\u201c和\u201d是哪位传承人\u201d'",
     "'是否主传承人这三个属性必须同时知道\u201c是哪个项目\u201d和\u201c是哪位传承人\u201d'"),
    ("'。项目表只存\u201c所属分类ID\u201c，'", "'。项目表只存\u201c所属分类ID\u201d，'"),
    ("'不存\u201d分类名称\u201c；作品表只存\u201d所属项目ID\u201c与'",
     "'不存\u201c分类名称\u201d；作品表只存\u201c所属项目ID\u201d与'"),
    ("'\u201c创作传承人ID\u201c，不存项目名称与传承人姓名。'",
     "'\u201c创作传承人ID\u201d，不存项目名称与传承人姓名。'"),
    ("'若同时存储ID与名称，则\u201d分类名称\u201c会通过'",
     "'若同时存储ID与名称，则\u201c分类名称\u201d会通过'"),
    ("'\u201c分类ID\u201c传递依赖于项目主键，违反 3NF。'",
     "'\u201c分类ID\u201d传递依赖于项目主键，违反 3NF。'"),
    ("'。活动表不存储\u201d已报名人数\u201c，'", "'。活动表不存储\u201c已报名人数\u201d，'"),
    ("'存储它会造成\u201d通过活动主键 → 活动主键 → 报名记录数\u201d'",
     "'存储它会造成\u201c通过活动主键 → 活动主键 → 报名记录数\u201d'"),
]

src = io.open(TARGET, encoding='utf-8').read()
changed = 0
for old, new in PAIRS:
    if old in src:
        src = src.replace(old, new)
        changed += 1
        print('  [OK ] 替换成功: %s' % old[:44])
    else:
        print('  [MISS] 未找到: %s' % old[:44])

io.open(TARGET, 'w', encoding='utf-8').write(src)
print('\n共修正 %d 处，开引号 %d 个 / 闭引号 %d 个'
      % (changed, src.count('\u201c'), src.count('\u201d')))
