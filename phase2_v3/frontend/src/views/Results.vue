<template>
  <div class="results-page">
    <!-- 头部 -->
    <div class="results-header">
      <button class="btn btn-outline" @click="$router.push('/')">← 返回工作台</button>
      <div class="results-title">
        <h2>执行结果</h2>
        <span class="run-id-badge">{{ runId }}</span>
        <span class="badge" :class="'badge-' + (runData.status || 'queued').toLowerCase()">
          {{ statusText(runData.status) }}
        </span>
      </div>
      <button class="btn btn-sm btn-outline" @click="fetchRun" :disabled="loading">🔄 刷新</button>
    </div>

    <div v-if="loading" class="loading-overlay">
      <span class="spinner"></span> 加载结果数据...
    </div>

    <div v-else-if="loadError" class="alert alert-error">{{ loadError }}</div>

    <template v-else>
      <!-- 轮询提示 -->
      <div v-if="runData.status === 'RUNNING' || runData.status === 'COMPILING'" class="alert alert-info">
        任务仍在{{ runData.status === 'COMPILING' ? '编译' : '执行' }}中，页面将自动刷新...
      </div>

      <!-- Tabs -->
      <div class="tabs">
        <div
          v-for="t in tabs"
          :key="t.key"
          class="tab-item"
          :class="{ active: activeTab === t.key }"
          @click="activeTab = t.key"
        >
          {{ t.icon }} {{ t.label }}
        </div>
      </div>

      <!-- Tab Content -->
      <MatchTab v-if="activeTab === 'match'" :runData="runData" :runId="runId" />
      <AuditTab v-else-if="activeTab === 'audit'" :runData="runData" />
      <FormatTab v-else-if="activeTab === 'format'" />
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api/index.js'
import MatchTab from '../components/results/MatchTab.vue'
import AuditTab from '../components/results/AuditTab.vue'
import FormatTab from '../components/results/FormatTab.vue'

const props = defineProps({ runId: { type: String, required: true } })
const route = useRoute()

const runData = ref({})
const loading = ref(true)
const loadError = ref('')
const activeTab = ref(route.query.tab || 'match')
let pollTimer = null

const tabs = [
  { key: 'match', label: '匹配结果', icon: '📊' },
  { key: 'audit', label: '全景稽核', icon: '🔍' },
  { key: 'format', label: '格式规范', icon: '📋' },
]

const STATUS_MAP = {
  QUEUED: '排队中', COMPILING: '编译中', PENDING_REVIEW: '待审批',
  RUNNING: '执行中', COMPLETED: '已完成', FAILED: '失败',
}

function statusText(s) { return STATUS_MAP[s] || s || '未知' }

async function fetchRun() {
  loadError.value = ''
  try {
    const data = await api.getRun(props.runId)
    runData.value = data
    loading.value = false

    // Auto-poll if still running/compiling
    if (data.status === 'RUNNING' || data.status === 'COMPILING') {
      startPolling()
    } else {
      stopPolling()
    }
  } catch (err) {
    loadError.value = '加载失败：' + (err.response?.data?.detail || err.message)
    loading.value = false
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(fetchRun, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(activeTab, (v) => {
  if (v !== route.query.tab) {
    // Sync tab to URL without full navigation
    window.history.replaceState(null, '', `?tab=${v}`)
  }
})

onMounted(() => fetchRun())
onUnmounted(() => stopPolling())
</script>

<style scoped>
.results-page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 16px 24px;
}

.results-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
}

.results-title {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
}

.results-title h2 {
  margin: 0;
  font-size: 20px;
  color: #1a237e;
}

.run-id-badge {
  font-family: 'Courier New', monospace;
  font-size: 12px;
  background: #e8eaf6;
  color: #1a237e;
  padding: 3px 10px;
  border-radius: 12px;
}

@media (max-width: 768px) {
  .results-page { padding: 12px; }
}
</style>
