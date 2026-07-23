<template>
  <div class="workbench-page">
    <!-- 新手引导条（可关闭，localStorage 记忆） -->
    <div v-if="showGuide" class="guide-bar">
      <span class="guide-text">
        🧭 <b>怎么用？</b>
        对数据文件做<b>筛选 / 核对 / 生成底稿报告</b> → 左侧上传文件并提交审计意图，中间跟踪任务进度与历史快照；
        查<b>法规准则</b> → 右侧知识问答；
        对复杂问题做<b>深度分析推理与报告撰写</b> →
        <router-link to="/agent" class="guide-link">🤖 智能分析</router-link>
      </span>
      <button class="guide-close" title="不再提示" @click="dismissGuide">✕</button>
    </div>

    <div class="workbench">
      <div class="wb-left">
        <FileUpload v-model="files" />
        <IntentInput
          :loading="submitting"
          @submit="handleSubmit"
        />
      </div>
      <div class="wb-center">
        <RunList
          :runs="runs"
          :loading="loadingRuns"
          @view-detail="goResults"
          @review="goReview"
          @delete="handleDeleteRun"
          @refresh="fetchRuns"
        />
      </div>
      <div class="wb-right">
        <RagSearch />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api/index.js'
import FileUpload from '../components/workbench/FileUpload.vue'
import IntentInput from '../components/workbench/IntentInput.vue'
import RunList from '../components/workbench/RunList.vue'
import RagSearch from '../components/shared/RagSearch.vue'

const router = useRouter()

const files = ref([])
const runs = ref([])
const loadingRuns = ref(false)
const submitting = ref(false)

// ── 新手引导条（关闭后 localStorage 记忆，不再打扰） ──
const showGuide = ref(localStorage.getItem('wb_guide_dismissed') !== '1')
function dismissGuide() {
  showGuide.value = false
  localStorage.setItem('wb_guide_dismissed', '1')
}

async function fetchRuns(silent = false) {
  if (!silent) loadingRuns.value = true
  try {
    const data = await api.listRuns()
    const sorted = (data.runs || []).sort((a, b) => {
      const da = a.created_at || ''
      const db = b.created_at || ''
      return db.localeCompare(da)
    })
    const oldKey = JSON.stringify(runs.value.map(r => [r.run_id, r.status, r.subject, r.created_at]))
    const newKey = JSON.stringify(sorted.map(r => [r.run_id, r.status, r.subject, r.created_at]))
    if (oldKey !== newKey) {
      runs.value = sorted
    }
  } catch (err) {
    runs.value = []
  } finally {
    if (!silent) loadingRuns.value = false
  }
}

// ── 自动轮询：有编译中/排队中的任务时每 5 秒刷新 ──
let _pollTimer = null
const POLL_INTERVAL = 5000

function startPolling() {
  if (_pollTimer) return
  _pollTimer = setInterval(async () => {
    await fetchRuns(true)
    // 没有需要轮询的任务时自动停止
    if (!runs.value.some(r => r.status === 'COMPILING' || r.status === 'QUEUED')) {
      stopPolling()
    }
  }, POLL_INTERVAL)
}

function stopPolling() {
  if (_pollTimer) {
    clearInterval(_pollTimer)
    _pollTimer = null
  }
}

// 监听 runs 变化：出现编译中/排队中的任务时自动开始轮询
watch(runs, (list) => {
  const needPoll = list.some(r => r.status === 'COMPILING' || r.status === 'QUEUED')
  if (needPoll) startPolling()
  else stopPolling()
}, { deep: false })

onUnmounted(stopPolling)

async function handleSubmit({ intent, projectCode, subject, presetButton }) {
  if (!files.value.length) {
    alert('请先上传文件')
    return
  }
  if (!intent.trim()) {
    alert('请输入审计意图')
    return
  }

  // 格式与纠错 → 跳转到 Results 页 FormatTab（走 /format/normalize API，非 DAG 编译）
  if (presetButton === '格式与纠错') {
    alert('格式规范化与纠错功能请进入 Run 详情页 → 格式规范 Tab 使用')
    return
  }

  submitting.value = true
  try {
    const fd = new FormData()
    fd.append('project_code', projectCode || 'PROJ_2026_001')
    fd.append('subject', subject || intent.slice(0, 20))
    fd.append('user_intent', intent)
    if (presetButton) fd.append('preset_button', presetButton)

    for (const f of files.value) {
      fd.append('files', f)
    }

    const result = await api.createRun(fd)
    alert(`Run 已创建：${result.run_id}\n状态：${result.status}`)
    files.value = []
    await fetchRuns()
  } catch (err) {
    alert('提交失败：' + (err.response?.data?.detail || err.message))
  } finally {
    submitting.value = false
  }
}

function goResults(runId) {
  router.push(`/results/${runId}`)
}

function goReview(runId) {
  router.push(`/review/${runId}`)
}

async function handleDeleteRun(runId) {
  if (!confirm(`确认删除 Run ${runId}？`)) return
  try {
    await api.deleteRun(runId)
    await fetchRuns()
  } catch (err) {
    alert('删除失败：' + (err.response?.data?.detail || err.message))
  }
}

onMounted(fetchRuns)
</script>

<style scoped>
.workbench-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 56px);
}

.guide-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 12px 16px 0;
  padding: 8px 14px;
  background: #e8eaf6;
  border: 1px solid #c5cae9;
  border-radius: 8px;
  font-size: 13px;
  color: #37474f;
  flex-shrink: 0;
}

.guide-text {
  flex: 1;
  line-height: 1.7;
}

.guide-link {
  color: #1a237e;
  font-weight: 600;
}

.guide-close {
  border: none;
  background: transparent;
  color: #90a4ae;
  cursor: pointer;
  font-size: 14px;
  flex-shrink: 0;
}

.guide-close:hover {
  color: #c62828;
}

/* 三栏比例：主操作区 400px（原 320px 过挤）/ 快照历史弹性 / 知识问答 340px（原 300px） */
.workbench {
  display: grid;
  grid-template-columns: 400px minmax(0, 1fr) 340px;
  gap: 16px;
  padding: 16px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.wb-left,
.wb-center,
.wb-right {
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow-y: auto;
}

@media (max-width: 1200px) {
  .workbench-page {
    height: auto;
  }

  .workbench {
    grid-template-columns: 1fr;
    grid-template-rows: auto auto auto;
    overflow: visible;
  }
}
</style>
