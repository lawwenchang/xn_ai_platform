<template>
  <div class="strategy-card card">
    <div class="card-header">
      🔧 策略详情（DAG 算子）
      <button class="btn btn-sm btn-outline" @click="collapsed = !collapsed">
        {{ collapsed ? '展开' : '折叠' }}
      </button>
    </div>

    <div v-if="!collapsed">
      <div v-if="operators.length === 0" class="empty-hint">无算子数据</div>

      <div v-for="(op, i) in operators" :key="op.id || i" class="operator-block">
        <div class="operator-header">
          <span class="op-index">{{ i + 1 }}</span>
          <span class="op-name">{{ op.name }}</span>
          <span class="op-id">{{ op.id }}</span>
          <span v-if="op.description" class="op-desc">{{ op.description }}</span>
        </div>

        <!-- 参数列表 -->
        <div v-if="op.params && Object.keys(op.params).length" class="operator-params">
          <div v-for="(val, key) in op.params" :key="key" class="param-row">
            <span class="param-key">{{ key }}</span>
            <span class="param-value">{{ formatParam(val) }}</span>
          </div>
        </div>

        <!-- source_file -->
        <div v-if="op.source_file" class="operator-source">
          📂 {{ op.source_file }}
        </div>

        <!-- output_alias -->
        <div v-if="op.output_alias" class="operator-output">
          → {{ op.output_alias }}
        </div>
      </div>
    </div>

    <!-- DAG JSON 原始数据 -->
    <details v-if="!collapsed" class="dag-json-detail">
      <summary>查看原始 DAG JSON</summary>
      <pre class="dag-json">{{ JSON.stringify(dag, null, 2) }}</pre>
    </details>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  dag: { type: Object, default: () => ({}) },
})

const collapsed = ref(false)

const operators = computed(() => {
  return props.dag.operators || []
})

function formatParam(val) {
  if (typeof val === 'object') return JSON.stringify(val, null, 2)
  return String(val)
}
</script>

<style scoped>
.operator-block {
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 10px;
  background: #fafafa;
}

.operator-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.op-index {
  width: 24px;
  height: 24px;
  background: #1a237e;
  color: #fff;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}

.op-name {
  font-weight: 700;
  font-size: 14px;
  color: #1a237e;
}

.op-id {
  font-family: 'Courier New', monospace;
  font-size: 11px;
  color: #888;
  background: #eee;
  padding: 1px 6px;
  border-radius: 4px;
}

.op-desc {
  font-size: 13px;
  color: #555;
  margin-left: auto;
}

.operator-params {
  margin-top: 6px;
}

.param-row {
  display: flex;
  gap: 8px;
  padding: 3px 0;
  font-size: 13px;
}

.param-key {
  font-weight: 600;
  color: #0d47a1;
  min-width: 120px;
  flex-shrink: 0;
}

.param-value {
  color: #333;
  font-family: 'Courier New', monospace;
  font-size: 12px;
  word-break: break-all;
}

.operator-source,
.operator-output {
  font-size: 12px;
  color: #666;
  margin-top: 4px;
}

.dag-json-detail {
  margin-top: 12px;
}

.dag-json-detail summary {
  cursor: pointer;
  font-size: 13px;
  color: #1a237e;
}

.dag-json {
  font-family: 'Courier New', monospace;
  font-size: 11px;
  background: #f5f6fa;
  padding: 12px;
  border-radius: 6px;
  max-height: 300px;
  overflow: auto;
  margin-top: 8px;
}

.empty-hint {
  text-align: center;
  color: #bbb;
  padding: 20px;
}
</style>
