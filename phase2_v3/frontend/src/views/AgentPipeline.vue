<template>
  <div class="agent-page">
    <div class="agent-grid">
      <!-- 左：任务输入区 -->
      <div class="left-col">
        <div class="card">
          <div class="card-header">🤖 多Agent智能分析</div>
          <p class="hint">
            复杂问题自动拆解：四个专业 Agent 串行协作，前序输出自动注入后序上下文，
            一句话完成「问题抽取 → 逻辑推理 → 法规检索 → 报告撰写」全链条。
          </p>
          <p class="hint">
            💡 本页适合<b>深度分析、推理与报告撰写</b>（产出分析结论文本）；
            如需对数据文件执行筛选、核对并产出底稿/结果文件，请前往
            <router-link to="/" style="color: #1a237e; font-weight: 600">🏠 工作台</router-link>。
          </p>

          <textarea
            v-model="message"
            rows="4"
            placeholder="例：分析这份底稿，找出金额异常和勾稽不一致的问题，检索对应审计准则，并生成审计报告"
            :disabled="running"
          ></textarea>

          <div class="file-zone">
            <label class="btn btn-outline btn-sm">
              📎 添加文件
              <input type="file" multiple hidden :accept="accept" :disabled="running" @change="onPick" />
            </label>
            <span class="file-hint">支持 md / txt / xlsx / xls / csv / docx / doc</span>
          </div>
          <ul v-if="files.length" class="file-list">
            <li v-for="(f, i) in files" :key="i">
              <span class="f-name">📄 {{ f.name }}</span>
              <button class="btn-x" :disabled="running" @click="files.splice(i, 1)">✕</button>
            </li>
          </ul>

          <div class="stage-picker">
            <div class="picker-label">执行阶段：</div>
            <label v-for="s in stageDefs" :key="s.key" class="stage-check">
              <input v-model="s.enabled" type="checkbox" :disabled="running" />
              {{ s.icon }} {{ s.label }}
            </label>
          </div>

          <button class="btn btn-primary run-btn" :disabled="!canRun" @click="run">
            <span v-if="running" class="spinner"></span>
            {{ running ? `管线执行中 ${elapsed}s（约需1-5分钟）` : '🚀 启动多Agent管线' }}
          </button>

          <div v-if="error" class="alert alert-error">{{ error }}</div>
          <div v-if="doneSeconds" class="alert alert-info">
            ✅ 管线完成，后端耗时 {{ doneSeconds }}s
          </div>
        </div>
      </div>

      <!-- 右：管线执行可视化 -->
      <div class="right-col">
        <div v-if="!visibleStages.length" class="card empty-card">请至少勾选一个执行阶段</div>
        <div v-for="(s, idx) in visibleStages" :key="s.key" class="stage-wrap">
          <div class="card stage-card" :class="'st-' + s.status">
            <div class="stage-head">
              <span class="stage-icon">{{ s.icon }}</span>
              <span class="stage-title">Stage {{ idx + 1 }} · {{ s.label }}Agent</span>
              <span class="badge" :class="'bs-' + s.status">{{ statusText(s.status) }}</span>
            </div>

            <!-- 问题抽取：结构化表格 -->
            <template v-if="s.key === 'issue_extractor' && s.parsed">
              <table class="mini-table">
                <thead>
                  <tr><th>ID</th><th>类别</th><th>严重度</th><th>问题描述</th></tr>
                </thead>
                <tbody>
                  <tr v-for="f in s.parsed.findings || []" :key="f.id">
                    <td class="mono">{{ f.id }}</td>
                    <td>{{ f.category }}</td>
                    <td><span class="sev" :class="'sev-' + String(f.severity || '').toLowerCase()">{{ f.severity }}</span></td>
                    <td>
                      <div class="f-title">{{ f.title }}</div>
                      <div class="f-detail">{{ f.detail }}</div>
                    </td>
                  </tr>
                </tbody>
              </table>
              <p v-if="s.parsed.summary" class="stage-summary">📌 {{ s.parsed.summary }}</p>
            </template>

            <!-- 逻辑推理：根因卡片 -->
            <template v-else-if="s.key === 'logic_reasoner' && s.parsed">
              <div v-for="(a, i) in s.parsed.analysis || []" :key="i" class="reason-item">
                <div class="r-head mono">{{ a.finding_id }}</div>
                <div class="r-row"><b>根因：</b>{{ a.root_cause }}</div>
                <div class="r-row"><b>影响：</b>{{ a.business_impact }}</div>
                <div v-if="a.risk_assessment" class="r-row"><b>风险：</b>{{ a.risk_assessment }}</div>
              </div>
              <p v-if="s.parsed.overall_assessment" class="stage-summary">📌 {{ s.parsed.overall_assessment }}</p>
            </template>

            <!-- 法规检索：准则匹配 -->
            <template v-else-if="s.key === 'regulation_searcher' && s.parsed">
              <div v-for="(r, i) in s.parsed.regulations || []" :key="i" class="reason-item">
                <div class="r-head"><span class="mono">{{ r.finding_id }}</span> ｜ {{ r.standard }}</div>
                <div v-if="r.requirement" class="r-row"><b>准则要求：</b>{{ r.requirement }}</div>
                <div v-if="r.compliance_gap" class="r-row"><b>合规差距：</b>{{ r.compliance_gap }}</div>
                <div v-if="r.recommendation" class="r-row"><b>建议：</b>{{ r.recommendation }}</div>
              </div>
              <p v-if="s.parsed.summary" class="stage-summary">📌 {{ s.parsed.summary }}</p>
            </template>

            <!-- 报告 / JSON 解析失败时的原文 -->
            <template v-else-if="s.raw">
              <pre class="report-text">{{ s.raw }}</pre>
              <div v-if="s.key === 'report_writer'" class="report-actions">
                <button class="btn btn-sm btn-outline" @click="copyText(s.raw)">📋 复制</button>
                <button class="btn btn-sm btn-primary" @click="downloadReport(s.raw)">⬇️ 下载 .md</button>
              </div>
            </template>

            <div v-else-if="s.status === 'running'" class="stage-placeholder">
              <span class="spinner"></span> Agent 推理中...
            </div>
            <div v-else class="stage-placeholder">等待执行</div>
          </div>
          <div v-if="idx < visibleStages.length - 1" class="flow-arrow">⬇ 输出注入下一Agent</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'
import api from '../api/index.js'

const accept = '.md,.txt,.xlsx,.xls,.csv,.docx,.doc'
const message = ref('')
const files = ref([])
const running = ref(false)
const error = ref('')
const elapsed = ref(0)
const doneSeconds = ref(0)
let timer = null

const stageDefs = ref([
  { key: 'issue_extractor', label: '问题抽取', icon: '🔍', enabled: true, status: 'idle', raw: '', parsed: null, resultKey: 'issue_extraction' },
  { key: 'logic_reasoner', label: '逻辑推理', icon: '🧩', enabled: true, status: 'idle', raw: '', parsed: null, resultKey: 'logic_analysis' },
  { key: 'regulation_searcher', label: '法规检索', icon: '📖', enabled: true, status: 'idle', raw: '', parsed: null, resultKey: 'regulation_match' },
  { key: 'report_writer', label: '报告撰写', icon: '📝', enabled: true, status: 'idle', raw: '', parsed: null, resultKey: 'final_report' },
])

const visibleStages = computed(() => stageDefs.value.filter((s) => s.enabled))
const canRun = computed(() => !running.value && message.value.trim() && visibleStages.value.length)

function onPick(e) {
  for (const f of e.target.files) files.value.push(f)
  e.target.value = ''
}

function statusText(s) {
  return { idle: '待执行', running: '执行中', done: '已完成', fail: '失败' }[s] || s
}

function tryParseJson(text) {
  if (!text) return null
  let t = text.replace(/<think>[\s\S]*?<\/think>/g, '').replace(/```(?:json)?/g, '').trim()
  const m = t.match(/\{[\s\S]*\}/)
  if (!m) return null
  try { return JSON.parse(m[0]) } catch { return null }
}

async function run() {
  error.value = ''
  doneSeconds.value = 0
  running.value = true
  elapsed.value = 0
  timer = setInterval(() => elapsed.value++, 1000)
  for (const s of stageDefs.value) {
    s.raw = ''
    s.parsed = null
    s.status = s.enabled ? 'running' : 'idle'
  }
  try {
    const fd = new FormData()
    fd.append('message', message.value.trim())
    fd.append('stages', visibleStages.value.map((s) => s.key).join(','))
    for (const f of files.value) fd.append('files', f)

    const res = await api.agentPipeline(fd)
    if (!res.success) throw new Error(res.error || '管线执行失败')

    const data = res.data || {}
    for (const s of stageDefs.value) {
      if (!s.enabled) continue
      s.raw = (data[s.resultKey] || '').trim()
      s.parsed = s.key === 'report_writer' ? null : tryParseJson(s.raw)
      s.status = s.raw ? 'done' : 'fail'
    }
    doneSeconds.value = res.elapsed_seconds || elapsed.value
  } catch (err) {
    error.value = '执行失败：' + (err.response?.data?.detail || err.message)
    for (const s of stageDefs.value) if (s.status === 'running') s.status = 'fail'
  } finally {
    running.value = false
    clearInterval(timer)
  }
}

function copyText(text) {
  if (navigator.clipboard) navigator.clipboard.writeText(text)
}

function downloadReport(text) {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `多Agent审计分析报告_${new Date().toISOString().slice(0, 10)}.md`
  a.click()
  URL.revokeObjectURL(a.href)
}

onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.agent-page {
  max-width: 1280px;
  margin: 0 auto;
  padding: 16px 24px;
}

.agent-grid {
  display: grid;
  grid-template-columns: 400px 1fr;
  gap: 16px;
  align-items: start;
}

.hint {
  font-size: 12px;
  color: #78909c;
  line-height: 1.6;
  margin: 8px 0 12px;
}

textarea {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
}

.file-zone {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 12px 0 6px;
}

.file-zone .btn {
  cursor: pointer;
}

.file-hint {
  font-size: 11px;
  color: #b0bec5;
}

.file-list {
  list-style: none;
  padding: 0;
  margin: 6px 0;
}

.file-list li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  padding: 4px 8px;
  background: #f5f6fa;
  border-radius: 6px;
  margin-bottom: 4px;
}

.btn-x {
  border: none;
  background: transparent;
  color: #ef5350;
  cursor: pointer;
}

.stage-picker {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  align-items: center;
  margin: 12px 0;
}

.picker-label {
  font-size: 13px;
  color: #546e7a;
  font-weight: 600;
}

.stage-check {
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
}

.run-btn {
  width: 100%;
  margin-top: 4px;
}

.empty-card {
  text-align: center;
  color: #b0bec5;
  padding: 40px;
}

.stage-wrap {
  margin-bottom: 4px;
}

.stage-card {
  border-left: 4px solid #cfd8dc;
  transition: border-color 0.3s;
}

.stage-card.st-running {
  border-left-color: #ffb300;
}

.stage-card.st-done {
  border-left-color: #43a047;
}

.stage-card.st-fail {
  border-left-color: #e53935;
}

.stage-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.stage-icon {
  font-size: 18px;
}

.stage-title {
  font-weight: 600;
  font-size: 14px;
  color: #1a237e;
  flex: 1;
}

.badge {
  font-size: 11px;
  padding: 2px 10px;
  border-radius: 10px;
}

.bs-idle { background: #eceff1; color: #78909c; }
.bs-running { background: #fff8e1; color: #f57f17; }
.bs-done { background: #e8f5e9; color: #2e7d32; }
.bs-fail { background: #ffebee; color: #c62828; }

.mini-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.mini-table th,
.mini-table td {
  border: 1px solid #eceff1;
  padding: 6px 8px;
  text-align: left;
  vertical-align: top;
}

.mini-table th {
  background: #f5f6fa;
  color: #546e7a;
}

.mono {
  font-family: 'Courier New', monospace;
}

.sev {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 8px;
}

.sev-high { background: #ffebee; color: #c62828; }
.sev-medium { background: #fff8e1; color: #f57f17; }
.sev-low { background: #e8f5e9; color: #2e7d32; }

.f-title {
  font-weight: 600;
}

.f-detail {
  color: #78909c;
  margin-top: 2px;
}

.reason-item {
  border: 1px solid #eceff1;
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 8px;
  font-size: 13px;
}

.r-head {
  font-weight: 600;
  color: #1a237e;
  margin-bottom: 4px;
}

.r-row {
  color: #455a64;
  line-height: 1.6;
}

.stage-summary {
  font-size: 13px;
  color: #37474f;
  background: #f5f6fa;
  border-radius: 6px;
  padding: 8px 12px;
  margin: 10px 0 0;
  line-height: 1.6;
}

.report-text {
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-word;
  background: #fafafa;
  border: 1px solid #eceff1;
  border-radius: 6px;
  padding: 12px 16px;
  max-height: 480px;
  overflow-y: auto;
  line-height: 1.7;
}

.report-actions {
  margin-top: 8px;
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.stage-placeholder {
  color: #b0bec5;
  font-size: 13px;
  padding: 12px 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.flow-arrow {
  text-align: center;
  color: #90a4ae;
  font-size: 12px;
  padding: 2px 0 6px;
}

@media (max-width: 1024px) {
  .agent-grid {
    grid-template-columns: 1fr;
  }
}
</style>
