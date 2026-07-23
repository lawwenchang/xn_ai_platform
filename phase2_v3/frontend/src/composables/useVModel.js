import { computed } from 'vue'

/**
 * Vue 3 v-model helper for Composition API.
 * Two-way binding between parent and child via
 * props.modelValue + emit('update:modelValue', ...).
 */
export function useVModel(props, emit) {
  return computed({
    get: () => props.modelValue,
    set: (val) => emit('update:modelValue', val),
  })
}
