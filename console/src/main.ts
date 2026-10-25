import { createApp } from 'vue'
import App from './App.vue'
import './style.css'
import { initializeTextSize } from './textSize'
initializeTextSize()
createApp(App).mount('#app')
