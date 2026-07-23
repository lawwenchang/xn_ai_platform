<template>
  <div>
    <!-- 指标卡片 -->
    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-value">{{ outputFiles.length }}</div>
        <div class="metric-label">输出文件</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{{ operatorCount }}</div>
        <div class="metric-label">执行算子</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{{ runData.retry_count || 0 }}</div>
        <div class="metric-label">重试次数</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{{ allPassed ? '✅' : '⏳' }}</div>
        <div class="metric-label">验证状态</div>
      </div>
    </div>

    <!-- 执行计划 -->
    <div v-if="executionPlan && executionPlan !== '未生成执行计划'" class="card">
      <div class="card-header">📜 执行计划</div>
      <pre class="plan-text">{{ executionPlan }}</pre>
    </div>

    <!-- 输出文件列表 -->
    <div v-if="outputFiles.length" class="card">
      <div class="card-header">📦 输出文件（{{ outputFiles.length }}）</div>
      <ul class="output-list">
        <li v-for="(f, i) in outputFiles" :key="i" class="output-item">
          <span>{{ fileIcon(f) }}</span>
          <span>{{ f }}</span>
        </li>
      </ul>
    </div>

    <!-- 操作 -->
    <div class="actions">
      <button
        class="btn btn-primary"
        @click="$emit('retry', {})"
      >
        🔄 调整规则重跑
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  runData: { type: Object, default: () => ({}) },
  runId: { type: String, default: '' },
})

defineEmits(['retry'])

const outputFiles = computed(() => {
  const outs = props.runData.outputs || props.runData.output_files || []
  return outs
})

const operatorCount = computed(() => {
  const bp = props.runData.dag_blueprint
  if (!bp) return 0
  return (bp.operators || []).length
})

const executionPlan = computed(() => {
  return props.runData.execution_plan || ''
})

const allPassed = computed(() => {
  return props.runData.all_validations_passed || false
})

function fileIcon(name) {
  const ext = (name || '').split('.').pop()?.toLowerCase()
  if (['xlsx', 'xls'].includes(ext)) return '📊'
  if (ext === 'csv') return '📋'
  if (ext === 'docx') return '📝'
  if (ext === 'txt') return '📄'
  return '📎'
}
</script>

<style scoped>
.plan-text {
  font-family: 'Courier New', monospace;
  font-size: 13px;
  background: #f5f6fa;
  padding: 12px 16px;
  border-radius: 6px;
  white-space: pre-wrap;
  max-height: 300px;
  overflow-y: auto;
}

.output-list {
  list-style: none;
  max-height: 300px;
  overflow-y: auto;
}

.output-item {
  padding: 6px 0;
  border-bottom: 1px solid #f0f0f0;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.actions {
  margin-top: 16px;
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}
</style>
