"""
报告复核工具 v2.0 - 一键审计报告质量检查
用法:
    from core.report_reviewer import review_report
    df = review_report("审计报告.docx", "科目余额表.xlsx")
    # LLM 自动从环境变量 VLLM_TUNNEL_URL 读取，默认 localhost:18000
"""

import re, json, os
import pandas as pd
import requests
from pathlib import Path

VLLM_URL = os.environ.get("VLLM_TUNNEL_URL", "http://localhost:18000/v1/chat/completions")
VLLM_KEY = os.environ.get("VLLM_API_KEY", "EMPTY")
VLLM_MODEL_NAME = os.environ.get("VLLM_MODEL", "qwen3-235b")


class ReportReviewer:

    def __init__(self, report_path=None, tb_path=None, prior_tb_path=None):
        self.report_path = Path(report_path) if report_path else None
        self.tb_path = Path(tb_path) if tb_path else None
        self.prior_tb_path = Path(prior_tb_path) if prior_tb_path else None
        self.llm_url = VLLM_URL
        self.llm_key = VLLM_KEY
        self.llm_model = VLLM_MODEL_NAME
        self.tables = []
        self.tb = None
        self.prior_tb = None
        self.findings = []

    def set_llm(self, url=None, key=None, model=None):
        if url: self.llm_url = url
        if key: self.llm_key = key
        if model: self.llm_model = model

    def disable_llm(self):
        self.llm_url = None

    def _call_llm(self, prompt, max_tokens=2000):
        resp = requests.post(self.llm_url,
            headers={"Authorization": "Bearer " + self.llm_key, "Content-Type": "application/json"},
            json={"model": self.llm_model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.1, "max_tokens": max_tokens}, timeout=120)
        if resp.status_code == 200:
            return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        return ""

    def _parse_json(self, text):
        for left, right in [("[", "]"), ("{", "}")]:
            s = text.find(left)
            e = text.rfind(right) + 1
            if s >= 0 and e > s:
                try:
                    return json.loads(text[s:e])
                except:
                    pass
        return None

    # -- 表格提取 --
    def _extract_docx_tables(self):
        try:
            from docx import Document
        except ImportError:
            return
        doc = Document(str(self.report_path))
        for ti, table in enumerate(doc.tables):
            rows = [[cell.text.strip().replace(chr(10), " ") for cell in row.cells] for row in table.rows]
            if rows:
                df = pd.DataFrame(rows[1:], columns=rows[0]) if len(rows) > 1 else pd.DataFrame(rows)
                self.tables.append({"index": ti, "data": df})

    # -- 1. 合计=分项和 --
    def _check_table_formulas(self):
        for t in self.tables:
            df = t["data"]
            for col in df.columns:
                vals = pd.to_numeric(df[col], errors="coerce")
                if vals.count() < 3:
                    continue
                total_mask = df.iloc[:, 0].astype(str).str.contains("合计|总计|小计", na=False)
                if total_mask.sum() == 0:
                    continue
                tv = pd.to_numeric(df[total_mask].iloc[0][col], errors="coerce")
                if pd.isna(tv):
                    continue
                detail_sum = vals[~total_mask].dropna().sum()
                diff = round(abs(tv - detail_sum), 2)
                if diff > 0.1:
                    self.findings.append({"类别": "公式校验",
                        "位置": "表{}.{}".format(t["index"] + 1, col),
                        "检查项": "合计!=分项和", "报表值": tv,
                        "计算值": round(detail_sum, 2), "差异": diff, "结果": "异常"})

    # -- 2. 资产负债表勾稽 --
    def _check_balance_sheet_equation(self):
        if self.tb is None:
            return
        subj_col = amt_col = None
        for c in self.tb.columns:
            s = str(c)
            if any(kw in s for kw in ["科目", "名称"]):
                subj_col = c
            if any(kw in s for kw in ["期末余额", "余额", "金额"]):
                amt_col = c
        if subj_col is None or amt_col is None:
            return
        a = l = e = 0.0
        for _, row in self.tb.iterrows():
            name = str(row[subj_col])
            amt = float(pd.to_numeric(row[amt_col], errors="coerce") or 0)
            if name[:4].startswith("1"):
                a += amt
            elif name[:4].startswith("2"):
                l += amt
            elif name[:4].startswith(("3", "4")):
                e += amt
        diff = round(a - l - e, 2)
        self.findings.append({"类别": "报表勾稽", "位置": "资产负债表",
            "检查项": "资产=负债+权益", "资产合计": round(a, 2),
            "负债+权益": round(l + e, 2), "差异": diff,
            "结果": "通过" if abs(diff) < 0.5 else "异常"})

    # -- 3. 财务指标 --
    def _check_financial_ratios(self):
        if self.tb is None:
            return
        subj_col = amt_col = None
        for c in self.tb.columns:
            s = str(c)
            if any(kw in s for kw in ["科目", "名称"]):
                subj_col = c
            if any(kw in s for kw in ["期末余额", "余额", "金额"]):
                amt_col = c
        if subj_col is None or amt_col is None:
            return
        bal = {}
        for _, row in self.tb.iterrows():
            name = str(row[subj_col])
            amt = float(pd.to_numeric(row[amt_col], errors="coerce") or 0)
            for key in ["货币资金", "应收账款", "存货", "流动资产", "固定资产",
                        "资产总计", "流动负债", "负债合计", "所有者权益",
                        "营业收入", "净利润"]:
                if key in name:
                    bal[key] = bal.get(key, 0) + amt
        if "流动资产" in bal and "流动负债" in bal and bal["流动负债"] != 0:
            self.findings.append({"类别": "指标校验", "位置": "流动比率",
                "检查项": "流动资产/流动负债",
                "计算值": round(bal["流动资产"] / bal["流动负债"], 2), "结果": ""})
        if "负债合计" in bal and "资产总计" in bal and bal["资产总计"] != 0:
            self.findings.append({"类别": "指标校验", "位置": "资产负债率",
                "检查项": "负债/资产",
                "计算值": round(bal["负债合计"] / bal["资产总计"] * 100, 1), "结果": ""})

    # -- 4. 交叉校验 --
    def _cross_check(self):
        if self.tb is None or not self.tables:
            return
        subj_col = amt_col = None
        for c in self.tb.columns:
            s = str(c)
            if any(kw in s for kw in ["科目", "名称"]):
                subj_col = c
            if any(kw in s for kw in ["期末余额", "余额", "金额"]):
                amt_col = c
        if subj_col is None or amt_col is None:
            return
        tb_vals = {}
        for _, row in self.tb.iterrows():
            tb_vals[str(row[subj_col]).strip()] = float(
                pd.to_numeric(row[amt_col], errors="coerce") or 0)
        for t in self.tables:
            df = t["data"]
            if df.empty or len(df.columns) < 2:
                continue
            name_col = df.columns[0]
            for ci in range(1, len(df.columns)):
                for _, row in df.iterrows():
                    name = str(row[name_col]).strip()
                    amt = pd.to_numeric(
                        str(row[df.columns[ci]]).replace(",", "").replace(chr(65292), ""),
                        errors="coerce")
                    if pd.isna(amt):
                        continue
                    for tn, ta in tb_vals.items():
                        if name in tn or tn in name:
                            diff = round(abs(amt - ta), 2)
                            if diff > 0.5 and abs(ta) > 1:
                                self.findings.append({"类别": "交叉校验",
                                    "位置": "表{} {}".format(t["index"] + 1, name),
                                    "检查项": "报告vs科目余额表", "报表值": amt,
                                    "科目余额表值": ta, "差异": diff, "结果": "异常"})
                            break

    # -- 5. 异动分析 --
    def _variance_analysis(self):
        if self.tb is None or self.prior_tb is None:
            return
        subj_col = amt_col = None
        for c in self.tb.columns:
            if any(kw in str(c) for kw in ["科目", "名称"]):
                subj_col = c
            if any(kw in str(c) for kw in ["期末余额", "余额", "金额"]):
                amt_col = c
        if subj_col is None or amt_col is None:
            return
        curr = {}
        prior = {}
        for _, row in self.tb.iterrows():
            curr[str(row[subj_col]).strip()] = float(
                pd.to_numeric(row[amt_col], errors="coerce") or 0)
        for _, row in self.prior_tb.iterrows():
            prior[str(row[subj_col]).strip()] = float(
                pd.to_numeric(row[amt_col], errors="coerce") or 0)
        changes = []
        for name in curr:
            c = curr[name]
            p = prior.get(name, 0)
            if abs(c) < 100 and abs(p) < 100:
                continue
            chg = c - p
            pct = round(chg / abs(p) * 100, 1) if abs(p) > 1 else 999
            if abs(pct) > 20:
                changes.append({"科目": name, "本期": round(c, 2), "上期": round(p, 2),
                                "变动额": round(chg, 2), "变动率%": pct})
        changes.sort(key=lambda x: abs(x["变动额"]), reverse=True)
        for ch in changes[:30]:
            self.findings.append({"类别": "异动分析", "位置": ch["科目"],
                "检查项": "同比>20%", "本期": ch["本期"], "上期": ch["上期"],
                "变动额": ch["变动额"], "变动率%": ch["变动率%"], "结果": "需关注"})

    # -- 6. LLM 错别字/标点/术语 --
    def _extract_docx_text(self):
        try:
            from docx import Document
        except ImportError:
            return []
        doc = Document(str(self.report_path))
        return [p.text.strip() for p in doc.paragraphs if len(p.text.strip()) > 10]

    def _check_typos(self):
        if not self.llm_url or not self.report_path:
            return
        paras = self._extract_docx_text()
        if not paras:
            return
        chunks = []
        buf = ""
        for p in paras:
            if len(buf) + len(p) > 3000:
                chunks.append(buf)
                buf = p
            else:
                buf += chr(10) + p
        if buf:
            chunks.append(buf)
        prompt_tpl = chr(10).join([
            "你是审计报告校对专家。检查以下片段，找出:",
            "1.错别字 2.标点错误 3.专有名词不一致 4.会计术语不当",
            "用JSON数组返回: [{位置, 错误类型, 原文, 建议修改, 严重程度}]，无错误返回[]",
            "报告片段:", "{text}", "直接返回JSON数组:"])
        for chunk in chunks:
            try:
                resp = self._call_llm(prompt_tpl.format(text=chunk))
                if resp:
                    errs = self._parse_json(resp)
                    if errs and isinstance(errs, list):
                        for e in errs:
                            self.findings.append({
                                "类别": "错别字/标点",
                                "位置": e.get("位置", ""),
                                "检查项": e.get("错误类型", ""),
                                "原文": e.get("原文", ""),
                                "建议修改": e.get("建议修改", ""),
                                "严重程度": e.get("严重程度", ""),
                                "结果": "需修改"})
            except Exception as ex:
                print("[LLM错别字] " + str(ex))

    # -- 7. LLM 报表公式与勾稽验证 --
    def _check_formulas_llm(self):
        if not self.llm_url or not self.tables:
            return
        for t in self.tables[:5]:
            df = t["data"]
            rows_text = ["|".join(str(c) for c in df.columns)]
            for _, row in df.head(20).iterrows():
                rows_text.append("|".join(str(v) for v in row.values))
            table_text = chr(10).join(rows_text)
            prompt = chr(10).join([
                "你是审计报告复核专家。分析以下财务报表表格，验证:",
                "1. 合计行的数值是否等于各分项之和",
                "2. 是否存在应有的勾稽关系(如资产=负债+权益)",
                "3. 财务指标计算是否正确(如毛利率=毛利/收入)",
                "用JSON数组返回发现的问题: [{表格名, 检查项, 报表值, 应有值, 差异, 说明}]",
                "无问题返回[]",
                "表格:", table_text, "直接返回JSON数组:"])
            try:
                resp = self._call_llm(prompt, max_tokens=3000)
                if resp:
                    errs = self._parse_json(resp)
                    if errs and isinstance(errs, list):
                        for e in errs:
                            self.findings.append({
                                "类别": "LLM公式验证",
                                "位置": e.get("表格名", "表{}".format(t["index"] + 1)),
                                "检查项": e.get("检查项", ""),
                                "报表值": e.get("报表值", ""),
                                "应有值": e.get("应有值", ""),
                                "差异": e.get("差异", ""),
                                "结果": e.get("说明", "需复核")})
            except Exception as ex:
                print("[LLM公式] " + str(ex))

    # -- 主流程 --
    def run(self):
        print("[报告复核] 开始...")
        if self.tb_path:
            self.tb = pd.read_excel(str(self.tb_path))
        if self.prior_tb_path:
            self.prior_tb = pd.read_excel(str(self.prior_tb_path))
        if self.report_path:
            self._extract_docx_tables()
        self._check_balance_sheet_equation()
        self._check_financial_ratios()
        self._check_table_formulas()
        self._cross_check()
        self._variance_analysis()
        if self.llm_url and self.report_path:
            print("[报告复核] LLM: 错别字检查...")
            self._check_typos()
            print("[报告复核] LLM: 公式验证...")
            self._check_formulas_llm()
        df = pd.DataFrame(self.findings)
        n = len(df)
        na = (df.get("结果") == "异常").sum() if "结果" in df.columns else 0
        print("[报告复核] {}项: {}异常".format(n, na))
        return df


def review_report(report_docx=None, trial_balance_xlsx=None,
                  prior_tb_xlsx=None, output_dir=None):
    r = ReportReviewer(report_docx, trial_balance_xlsx, prior_tb_xlsx)
    df = r.run()
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        df.to_excel(str(out / "报告复核结果.xlsx"), index=False)
    return df