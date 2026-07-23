<template>
  <div class="review-page">
    <!-- 返回按钮 -->
    <div class="review-header">
      <button class="btn btn-outline" @click="$router.push('/')">← 返回工作台</button>
      <div class="review-title">
        <h2>方案审批</h2>
        <span class="run-id-badge">{{ runId }}</span>
        <span class="badge" :class="'badge-' + (runData.status || 'queued').toLowerCase()">
          {{ statusText(runData.status) }}
        </span>
      </div>
    </div>

    <div v-if="loading" class="loading-overlay">
      <span class="spinner"></span> 加载方案数据...
    </div>

    <div v-else-if="loadError" class="alert alert-error">{{ loadError }}</div>

    <template v-else>
      <!-- 平台提案说明 -->
      <div v-if="matchExplanation" class="card explanation-card">
        <div class="card-header">💡 平台提案说明</div>
        <div class="explanation-text">{{ matchExplanation }}</div>
      </div>

      <!-- 策略总览卡片 -->
      <div class="metrics-grid">
        <div class="metric-card">
          <div class="metric-value">{{ operatorCount }}</div>
          <div class="metric-label">物理算子</div>
        </div>
        <div class="metric-card">
          <div class="metric-value">{{ loadCount }}</div>
          <div class="metric-label">数据源文件</div>
        </div>
        <div class="metric-card">
          <div class="metric-value">{{ filterCount }}</div>
          <div class="metric-label">筛选/匹配步骤</div>
        </div>
        <div class="metric-card">
          <div class="metric-value">{{ aggregateCount }}</div>
          <div class="metric-label">汇总/对比步骤</div>
        </div>
      </div>

      <!-- 策略卡片（算子详情） -->
      <StrategyCard v-if="dagBlueprint" :dag="dagBlueprint" />

      <!-- 执行计划 -->
      <div v-if="executionPlan && executionPlan !== '未生成执行计划'" class="card">
        <div class="card-header">📜 执行计划</div>
        <pre class="plan-text">{{ executionPlan }}</pre>
      </div>

      <!-- 修改意见 -->
      <div class="card">
        <div class="card-header">📝 修改意见（可选）</div>
        <textarea
          v-model="feedback"
          placeholder="如需调整匹配策略、筛选条件或汇总维度，请在此说明..."
          rows="3"
        ></textarea>
      </div>

      <!-- 操作按钮 -->
      <div class="review-actions">
        <button class="btn btn-outline" @click="goBackToWorkbench">
          🔄 返回修改（新建 Run）
        </button>
        <button
          class="btn btn-success"
          :disabled="submitting"
          @click="confirmExecute"
        >
          <span v-if="submitting" class="spinner"></span>
          ✅ 确认执行
        </button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api/index.js'
import StrategyCard from '../components/review/StrategyCard.vue'

const props = defineProps({ runId: { type: String, required: true } })
const router = useRouter()

const runData = ref({})
const loading = ref(true)
const loadError = ref('')
const submitting = ref(false)
const feedback = ref('')

const STATUS_MAP = {
  QUEUED: '排队中',
  COMPILING: '编译中',
  PENDING_REVIEW: '待审批',
  RUNNING: '执行中',
  COMPLETED: '已完成',
  FAILED: '失败',
}

function statusText(s) {
  return STATUS_MAP[s] || s || '未知'
}

const dagBlueprint = computed(() => {
  return runData.value.dag_blueprint || null
})

const matchExplanation = computed(() => {
  const raw = runData.value.match_explanation || runData.value.dag_blueprint?.match_explanation || null
  if (!raw) return null
  // 去除模型思考标签 <think>...</think>
  return raw.replace(/<think>[\s\S]*?<\/think>\s*/g, '').trim() || null
})

const executionPlan = computed(() => {
  return runData.value.execution_plan || ''
})

const operators = computed(() => {
  const bp = dagBlueprint.value
  if (!bp) return []
  return bp.operators || []
})

const operatorCount = computed(() => operators.value.length)
const loadCount = computed(() => operators.value.filter((o) => o.name === 'Load').length)
const filterCount = computed(() =>
  operators.value.filter((o) => ['RegexFilter', 'ColumnFilter', 'ConditionCheck', 'Extract', 'Transform'].includes(o.name)).length
)
const aggregateCount = computed(() =>
  operators.value.filter((o) => ['Aggregate', 'Merge', 'Diff', 'GroupBy'].includes(o.name)).length
)

async function fetchRun() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await api.getRun(props.runId)
    runData.value = data
  } catch (err) {
    loadError.value = '加载失败：' + (err.response?.data?.detail || err.message)
  } finally {
    loading.value = false
  }
}

async function confirmExecute() {
  if (!confirm('确认执行此 DAG 方案？\n（执行后沙箱将自动运行算子并生成审计结果）')) return
  submitting.value = true
  try {
    const result = await api.executeRun(props.runId, true)
    alert(`执行已触发！\n状态：${result.status}`)
    router.push(`/results/${props.runId}`)
  } catch (err) {
    alert('执行失败：' + (err.response?.data?.detail || err.message))
  } finally {
    submitting.value = false
  }
}

function goBackToWorkbench() {
  router.push('/')
}

onMounted(fetchRun)
</script>


<style scoped>
.review-page {
  max-width: 1000px;
  margin: 0 auto;
  padding: 16px 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.review-header {
  display: flex;
  align-items: center;
  gap: 16px;
}

.review-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.review-title h2 {
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

.explanation-card {
  background: #e3f2fd;
  border-left: 4px solid #1565c0;
}

.explanation-text {
  font-size: 14px;
  line-height: 1.8;
  color: #333;
  white-space: pre-wrap;
}

.plan-text {
  font-family: 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  background: #f5f6fa;
  padding: 12px 16px;
  border-radius: 6px;
  white-space: pre-wrap;
  max-height: 300px;
  overflow-y: auto;
}

.review-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 16px 0;
}

@media (max-width: 768px) {
  .review-page {
    padding: 12px;
  }
}
</style>

