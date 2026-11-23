import re
import sys
from pathlib import Path

p = Path(sys.argv[1])
h = p.read_text(encoding="utf-8")

LEAD = {
    "一、选题背景": "本章先交代现实问题，再说明技术切入点，最后界定本选题在“工具开发”与“规则约束”之间的定位。",
    "二、项目目标": "本章给出总体目标，并把目标拆成九条可验证项与一份明确的范围边界——做什么、不做什么都写清楚。",
    "三、研究或开发内容": "本章是报告的主体，按数据库、AI 模块、服务层、前端、兼容层、演示模式、工程配套七个部分展开，每部分都指向具体的文件与代码。",
    "四、技术方案": "本章说明选型理由、系统架构、最关键的四层防护设计，以及测试策略与参数标定方法。",
    "五、可行性分析": "本章从技术、数据、经济与人力三个角度评估可行性，并把风险与应对措施逐条列出。",
    "六、预期成果": "本章列出四类可交付成果：可运行系统、可验证证据、工程文档、可复用的方法论。",
    "七、进度计划": "本章按实际开发顺序复盘已完成工作，记录开发中发现的四个缺陷，并给出后续优先级排布。",
}

n = 0
for title, lead in LEAD.items():
    pat = re.compile(r"(<h2>" + re.escape(title) + r"</h2>)\s*(<h3>)")
    if pat.search(h):
        h = pat.sub(lambda m, L=lead: m.group(1) + "\n    <p>" + L + "</p>\n\n    " + m.group(2), h, count=1)
        n += 1
        print("[OK]", title)
    else:
        print("[MISS]", title)

p.write_text(h, encoding="utf-8")
print("patched:", n)
