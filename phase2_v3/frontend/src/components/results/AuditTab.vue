<template>
  <div>
    <!-- 稽核统计 -->
    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-value" style="color: #b71c1c;">{{ criticalCount }}</div>
        <div class="metric-label">🔴 严重问题</div>
      </div>
      <div class="metric-card">
        <div class="metric-value" style="color: #e65100;">{{ warningCount }}</div>
        <div class="metric-label">🟠 警告</div>
      </div>
      <div class="metric-card">
        <div class="metric-value" style="color: #1565c0;">{{ infoCount }}</div>
        <div class="metric-label">🔵 信息提示</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{{ totalFindings }}</div>
        <div class="metric-label">总计发现</div>
      </div>
    </div>

    <!-- 问题清单 -->
    <div v-if="findings.length" class="findings-list">
      <div
        v-for="(f, i) in findings"
        :key="i"
        class="finding-card"
        :class="'severity-' + (f.severity || 'info')"
      >
        <div class="finding-header">
          <span class="severity-badge" :class="'sev-' + (f.severity || 'info')">
            {{ severityLabel(f.severity) }}
          </span>
          <span class="finding-title">{{ f.title || f.check || '稽核项 ' + (i + 1) }}</span>
          <span v-if="f.passed !== undefined" class="finding-status">
            {{ f.passed ? '✅' : '❌' }}
          </span>
        </div>
        <div v-if="f.description || f.detail" class="finding-body">
          {{ f.description || f.detail }}
        </div>
      </div>
    </div>

    <div v-else class="empty-hint">
      暂无稽核结果（Run 执行完成或失败后方可查看）
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  runData: { type: Object, default: () => ({}) },
})

const findings = computed(() => {
  // Try multiple sources: validation_results, findings from dag, execution_logs
  const vrs = props.runData.validation_results || []
  if (vrs.length) return vrs.map((v, i) => ({
    ...v,
    severity: v.severity || (v.passed ? 'info' : 'warning'),
    title: v.check || v.description || `稽核项 ${i + 1}`,
  }))

  // Fallback: extract potential findings from dag blueprint
  const bp = props.runData.dag_blueprint
  if (!bp) return []

  const ops = bp.operators || []
  return ops
    .filter((o) => ['ConditionCheck', 'Diff', 'AuditAdjustment', 'Reconcile'].includes(o.name))
    .map((o, i) => ({
      title: o.description || `${o.name} 算子结果`,
      description: o.params ? JSON.stringify(o.params) : '',
      severity: o.name === 'AuditAdjustment' ? 'critical' : 'warning',
      passed: null,
    }))
})

const criticalCount = computed(() => findings.value.filter((f) => f.severity === 'critical').length)
const warningCount = computed(() => findings.value.filter((f) => f.severity === 'warning').length)
const infoCount = computed(() => findings.value.filter((f) => f.severity === 'info' || !f.severity).length)
const totalFindings = computed(() => findings.value.length)

function severityLabel(s) {
  const map = { critical: '严重', warning: '警告', info: '信息' }
  return map[s] || '信息'
}
</script>

<style scoped>
.findings-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.finding-card {
  border-radius: 8px;
  padding: 14px;
  border-left: 4px solid #ccc;
}

.finding-card.severity-critical {
  background: #fff5f5;
  border-left-color: #c62828;
}

.finding-card.severity-warning {
  background: #fff8f0;
  border-left-color: #e65100;
}

.finding-card.severity-info {
  background: #f5f8ff;
  border-left-color: #1565c0;
}

.finding-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.severity-badge {
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
}

.sev-critical { background: #c62828; }
.sev-warning { background: #e65100; }
.sev-info { background: #1565c0; }

.finding-title {
  font-weight: 600;
  font-size: 14px;
  flex: 1;
}

.finding-status {
  font-size: 18px;
}

.finding-body {
  margin-top: 8px;
  font-size: 13px;
  color: #555;
  white-space: pre-wrap;
}

.empty-hint {
  text-align: center;
  color: #bbb;
  padding: 40px;
}
</style>
