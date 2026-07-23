import { createRouter, createWebHistory } from 'vue-router'
import Workbench from '../views/Workbench.vue'
import Review from '../views/Review.vue'
import Results from '../views/Results.vue'
import AgentPipeline from '../views/AgentPipeline.vue'

const routes = [
  { path: '/', name: 'Workbench', component: Workbench },
  { path: '/review/:runId', name: 'Review', component: Review, props: true },
  { path: '/results/:runId', name: 'Results', component: Results, props: true },
  { path: '/agent', name: 'AgentPipeline', component: AgentPipeline },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
