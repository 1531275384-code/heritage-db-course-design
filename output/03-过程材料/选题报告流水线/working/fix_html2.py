#!/usr/bin/env python3
"""第二次定向修正：补章节导语 + 补一份可验证资料索引（AP-02）。

做法：在每个"标题紧跟标题"的位置插入一句真实导语（不是水词，是承接上下文的过渡句）。
"""
import re
import sys
from pathlib import Path

SRC = Path(sys.argv[1])
html = SRC.read_text(encoding="utf-8")

# (紧跟的标题片段, 要插入的导语) —— 逐个按原文定位，避免误插
LEADS = [
    # 三、（一）数据库设计
    ('<h3>（一）数据库设计（主体工作量）</h3>\n\n    <h4>1. 八张表与结构决策</h4>',
     '<h3>（一）数据库设计（主体工作量）</h3>\n'
     '    <p>数据库设计是整个项目的主体工作量，先从八张表的结构决策讲起，再依次说明垂直拆分、索引、触发器、视图、权限五个层面的具体取舍。</p>\n\n'
     '    <h4>1. 八张表与结构决策</h4>'),
    # 四、（一）技术选型
    ('<h3>（一）技术选型与理由</h3>\n\n    <table',
     '<h3>（一）技术选型与理由</h3>\n'
     '    <p>下表逐层列出选型与实际理由。之所以把"为什么不选替代方案"也一并写出，是因为技术选型的说服力来自对比，而非罗列名词。</p>\n\n    <table'),
    # 四、（三）四层防护 → 1. 第一层
    ('<h3>（三）关键设计：AI 为什么改不了权属（四层防护）</h3>\n\n    <h4>1. 第一层',
     '<h3>（三）关键设计：AI 为什么改不了权属（四层防护）</h3>\n'
     '    <p>四层防护按"从硬到软"排列：数据库权限最硬，源码测试次之，模块隔离再次，触发器留痕兜底。下面逐层说明。</p>\n\n    <h4>1. 第一层'),
    # 五、（一）技术可行性
    ('<h3>（一）技术可行性</h3>\n\n    <table',
     '<h3>（一）技术可行性</h3>\n'
     '    <p>可行性从依赖成熟度、算法、性能三个角度评估，并明确区分"已验证"与"未验证"两部分。</p>\n\n    <table'),
    # 七、（一）已完成工作
    ('<h3>（一）已完成工作（按实际开发顺序）</h3>\n\n    <table',
     '<h3>（一）已完成工作（按实际开发顺序）</h3>\n'
     '    <p>下表按实际开发顺序列出十二个阶段。顺序本身说明了一个事实：数据库设计与脚本先于后端与前端完成，而不是边写接口边改表。</p>\n\n    <table'),
    # 三、（二）AI 模块 → 1. 三档后端
    ('<h3>（二）AI 模块：特征提取与分级建议</h3>\n\n    <h4>1. 三档特征后端与自动降级</h4>',
     '<h3>（二）AI 模块：特征提取与分级建议</h3>\n'
     '    <p>AI 模块只做两件事：把图像变成向量，把向量距离变成分级建议。前者要解决"环境依赖不确定"的问题，后者要解决"数字如何翻译成人能看懂的理由"的问题。</p>\n\n'
     '    <h4>1. 三档特征后端与自动降级</h4>'),
    # 三、（三）后端服务层 → 1. 分层
    ('<h3>（三）后端服务层</h3>\n\n    <h4>1. 分层与职责边界</h4>',
     '<h3>（三）后端服务层</h3>\n'
     '    <p>服务层是"业务规则与事务边界"的唯一归属地。下面先看分层，再看其中最关键的一个函数。</p>\n\n    <h4>1. 分层与职责边界</h4>'),
    # 六、（一）可运行系统
    ('<h3>（一）可运行系统</h3>\n\n    <table',
     '<h3>（一）可运行系统</h3>\n'
     '    <p>最终交付的不是一份设计文档，而是一个能启动、能点击、能截图的系统。以下三项构成它的可运行形态。</p>\n\n    <table'),
    # 六、（二）可验证证据
    ('<h3>（二）可验证证据</h3>\n\n    <table',
     '<h3>（二）可验证证据</h3>\n'
     '    <p>"能跑"需要证据支撑。以下四类证据均可由第三方独立复现，不依赖开发者的口头说明。</p>\n\n    <table'),
    # 七、（三）后续计划
    ('<h3>（三）后续计划（若继续推进）</h3>\n\n    <table',
     '<h3>（三）后续计划（若继续推进）</h3>\n'
     '    <p>按优先级排布后续工作。P0 两项都指向同一个目标——补上真实数据库验证这一缺口。</p>\n\n    <table'),
    # 五、（四）风险
    ('<h3>（四）主要风险与应对</h3>\n\n    <table',
     '<h3>（四）主要风险与应对</h3>\n'
     '    <p>风险按"后果严重程度"排序，而非按发生概率。第一项正是本报告反复强调的那处验证缺口。</p>\n\n    <table'),
    # 三、（五）兼容层后接（六）演示模式（h3 接 h3）
    ('<h3>（六）演示模式：完整可跑但诚实声明</h3>',
     '<h3>（六）演示模式：完整可跑但诚实声明</h3>'),
    # 三、（一）下的 4.触发器 → 5.三个视图 之间已有正文，不需处理
]

for old, new in LEADS:
    if old in html and old != new:
        html = html.replace(old, new)
        print("[OK] 已插入导语：", old.split("\n")[0][:42])
    elif old != new:
        print("[MISS]", old.split("\n")[0][:42])

# 三、（四）前端界面：h3 后面接 h2（下一个是（五））—— 补一句收尾
html = html.replace(
    '<h3>（五）pgvector 兼容层：本项目的额外设计增量</h3>\n    <p>',
    '<p>前端全部逻辑集中在一个 JS 文件内，不引入构建工具；这一取舍换来的是"打开首页即可用"的可靠性。</p>\n\n'
    '    <h3>（五）pgvector 兼容层：本项目的额外设计增量</h3>\n    <p>',
)

# ---------- AP-02：补可验证资料索引（以正文材料清单替代不符体裁的参考文献） ----------
materials = """  <div class="references" aria-label="附录：可验证资料索引">
    <h2>附录　可验证资料索引</h2>
    <p>本报告的全部结论均可对照工程资料核实。按类别列出主要文件及其可验证的内容：</p>
    <table class="three-line-table">
      <caption>表 A-1　可验证资料索引</caption>
      <thead>
        <tr><th style="width:30%">类别与该类文件</th><th>可验证的内容</th></tr>
      </thead>
      <tbody>
        <tr><td>数据库源码（1077 行）<br><code>sql/00_compat_vector.sql</code>／<code>01_init.sql</code>／<code>02_index_trigger_view.sql</code>／<code>03_seed.sql</code></td><td>8 表、24 条 CHECK、12 个外键、14 索引、7 触发器、3 视图、4 角色 GRANT；兼容层的余弦距离实现与 <code>\\if</code> 模式切换</td></tr>
        <tr><td>后端源码（约 3700 行）<br><code>app/services.py</code>／<code>api.py</code>／<code>db.py</code>／<code>auth.py</code>／<code>ai/</code> 三件套／<code>demo.py</code></td><td>终审 6 步单事务、<code>open_tx</code> 事务上下文、18 个端点、分级器纯函数隔离、三档特征后端、演示模式同名同签名实现</td></tr>
        <tr><td>前端源码（1588 行）<br><code>app/static/index.html</code>／<code>app.js</code></td><td>三区块界面、演示模式横幅、事件委托、<code>esc()</code> 转义、<code>nz()</code> 兜底</td></tr>
        <tr><td>配置与脚本（约 900 行）<br><code>bootstrap.py</code>／<code>scripts/init_db.py</code>／<code>docker-compose.yml</code>／<code>.env.example</code></td><td>四层数据库探测与修复命令、真库一键初始化、pgvector 容器定义、配置优先级</td></tr>
        <tr><td>测试（2025 行，192 项）<br><code>test_invariants.py</code>／<code>test_compat_vector.py</code>／<code>test_sql_structure.py</code>／<code>test_classifier.py</code>／<code>test_feature_engine.py</code>／<code>e2e_demo.py</code></td><td>源码不变量 64 项、兼容层数学一致性 39 项、SQL 结构校验、分级器 24 项、特征引擎 17 项、端到端 48 项（13 个业务场景）</td></tr>
        <tr><td>标定与复现脚本<br><code>tests/tune_weights.py</code>／<code>tests/shoot_ui.py</code>／<code>app/motif_images.py</code></td><td>特征段权重 0.51／0.23／0.26 的网格搜索过程、8 张截图的自动生成流程、母题与样例图的程序化绘制</td></tr>
        <tr><td>文档与实证（67 KB + 8 图）<br><code>README.md</code>／<code>docs/shots/01~08.png</code></td><td>数据库原理对照表、四层防护说明、已知限制与 4 条缺陷记录、全流程真实浏览器截图</td></tr>
      </tbody>
    </table>
    <p>其中 README 第 8 章为"数据库原理对照表"，按范式、索引、触发器、视图、事务、权限六个维度逐条对应到源码位置；第 13 章为"已知限制"，如实列出未验证项与工程简化。</p>
  </div>

"""
anchor = "  </main>\n</section>"
if anchor in html:
    html = html.replace(anchor, materials + anchor)
    print("[OK] 已插入附录资料索引")
else:
    print("[MISS] 未找到 </main> 锚点")

SRC.write_text(html, encoding="utf-8")
print("[DONE] 已写入", SRC)
