#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""routes.py 补丁 A：上传白名单扩展 + 审计领域知识重写 + DAG 幻觉熔断"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "api" / "routes.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")


def rep(old: str, new: str, tag: str):
    global src
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    print(f"  [PATCH] {tag}")


# ── 1. 上传白名单 + MIME ───────────────────────────────────────
rep('''ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".docx", ".doc", ".zip", ".rar", ".7z"}
ALLOWED_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",''',
    '''ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".docx", ".doc",
                      ".pdf", ".md", ".txt", ".zip", ".rar", ".7z"}
ALLOWED_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain", "text/markdown",
    "text/csv",''',
    "上传白名单+MIME 扩展 pdf/md/txt")

# ── 2. 审计领域知识重写（方向镜像/精确到分/四分类/文档能力） ───
rep('''### 银行流水核对最佳实践
1. 匹配策略优先级：先按金额精确匹配，再按日期±3天模糊匹配，最后按对方户名模糊匹配（阈值≥0.85）
2. 多列联合筛选优于单列：同时检查摘要、对方客户名称、附言等信息列
3. 金额列优先用单列"交易金额"（正=收入，负=支出），而非收入/支出双列
4. 业务筛选关键词：（根据实际业务列名推断）
5. 噪音排除：手续费、短信费、年费、利息、账户管理费、冲正、测试

### 跨表数据匹配原则
1. 优先识别两表共有的关键列（如机构名称、日期、金额）
2. 机构名称需标准化（去除市县区中心管理等后缀后再比对）
3. 金额比对需设定容差（默认1%，用户可自然语言指定如"差异控制在5万以内"）
4. 正负金额含义：正=收入，负=支出
5. 时间维度：按年度、季度、月份分段比对更精准''',
    '''### 银行对账第一常识（方向镜像，必须遵守）
1. 企业序时账/日记账：借方金额=银行存款增加，贷方金额=减少 → 净额=借方−贷方
2. 银行流水：贷方（收入）=存款增加，借方（支取）=减少 → 净额=贷方收入−借方支取
3. 双方记账方向互为镜像：序时账"借方金额"对应流水"贷方（收入）"，序时账"贷方金额"对应流水"借方（支取）"
4. 流水含多个银行账户、序时账为单账户时：必须先按银行账号过滤流水再对账

### 逐笔核对与容差（不得混淆）
1. 银行存款逐笔核对必须精确到分（±0.01 元），差一分钱都必须查明原因
2. 百分比容差（如 1%）只适用于汇总层面的分析性复核，绝不可用于逐笔核对
3. 匹配策略优先级：金额精确+同日 → 金额精确+日期窗口(±3天) → 同方向 n:m 合计相等（拆分/合并入账）→ 摘要/对方户名模糊（仅进人工复核，不自动确认）
4. 未匹配项默认"待人工核查"；只有接近期末且有窗口证据的才能列为未达账项候选
   （银收企未收/银付企未付/企收银未收/企付银未付），且需期后到账验证
5. 利息、手续费、冲正不删除、单独成类输出（问题解答第12号要求关注存款收益与规模匹配性）

### 跨表数据匹配原则
1. 优先识别两表共有的有业务含义的关键列；严禁使用"序号/编号/行号"作为连接键
2. 列名以 Data Catalog 中的真实列名为准，严禁照抄示例中的文件名/列名
3. 机构名称"去市县区中心管理后缀"的标准化仅用于医保回款场景，其他场景禁止
4. 金额汇总比对容差由用户指定（如"差异控制在5万以内"），逐笔核对一律 ±0.01 元
5. 支持文档格式输入（docx/doc/pdf/md/txt），文档中的表格与 Excel 同权参与比对''',
    "AUDIT_DOMAIN_KNOWLEDGE 专业化重写")

# ── 3. Dify 主链路：DAG 幻觉硬校验 + 规则修复熔断 ─────────────
rep('''    try:
        client = _get_http_client()
        response = await client.post(
            f"{DIFY_BASE_URL}/v1/workflows/run",''',
    '''    # 幻觉校验基准：Data Catalog 中的真实文件名
    _known_files = [f["filename"] for f in catalog.files] if catalog and catalog.files else []

    try:
        client = _get_http_client()
        response = await client.post(
            f"{DIFY_BASE_URL}/v1/workflows/run",''',
    "Dify 链路 known_files 基准")

rep('''            dag_for_return = DAGParser.parse(dag_json_str)
            return dag_for_return''',
    '''            try:
                dag_for_return = DAGParser.parse(dag_json_str, known_files=_known_files or None)
            except ValueError:
                # 幻觉熔断：确定性规则修复（difflib 就近纠正文件名）后重校验，
                # 仍失败则抛错进入上级熔断链（DAG 精修 → vLLM 直连 → 本地兜底）
                from core.dag_compiler import rule_fix_dag
                _fixed = rule_fix_dag(dag_json_str, known_files=_known_files or None)
                if _fixed:
                    dag_for_return = DAGParser.parse(_fixed, known_files=_known_files or None)
                else:
                    raise
            return dag_for_return''',
    "Dify 链路幻觉硬校验+熔断")

# ── 4. vLLM 降级链路：同样硬校验 ───────────────────────────────
rep('''    try:
        return DAGParser.parse(dag_json)
    except Exception as e:
        raise ValueError(f"DAG 解析失败: {e}")''',
    '''    # 幻觉校验基准：从 catalog_text 还原真实文件名（"文件: xxx" 行）
    _kf = re.findall(r"^文件[:：]\\s*(.+?)\\s*$", catalog_text, flags=re.M)
    try:
        return DAGParser.parse(dag_json, known_files=_kf or None)
    except Exception as e:
        from core.dag_compiler import rule_fix_dag
        _fixed = rule_fix_dag(dag_json, known_files=_kf or None)
        if _fixed:
            try:
                return DAGParser.parse(_fixed, known_files=_kf or None)
            except Exception:
                pass
        raise ValueError(f"DAG 解析失败: {e}")''',
    "vLLM 降级链路幻觉硬校验")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("routes 补丁A 完成，AST OK")
