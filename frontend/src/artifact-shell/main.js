// Entry for the dedicated artifact page's trusted shell (design §5/§9). It
// mounts a deliberately MINIMAL Vue app — only Vue + the shared broker
// composable/prompt + the few Web Awesome components the prompt renders — so the
// bundle stays small and pulls in none of the main SPA. The backend serves this
// at /_twicc/artifact-shell/shell.js and injects the per-bookmark data the app
// reads below.

import { createApp } from 'vue'

// Web Awesome: design tokens + ONLY the components the consent prompt uses.
import '@awesome.me/webawesome/dist/styles/webawesome.css'
import '@awesome.me/webawesome/dist/styles/themes/default.css'
import '@awesome.me/webawesome/dist/components/dialog/dialog.js'
import '@awesome.me/webawesome/dist/components/button/button.js'
import '@awesome.me/webawesome/dist/components/callout/callout.js'
import '@awesome.me/webawesome/dist/components/icon/icon.js'

import ArtifactShellApp from './ArtifactShellApp.vue'

const dataEl = document.getElementById('twicc-shell-data')
const shellData = dataEl ? JSON.parse(dataEl.textContent) : {}

createApp(ArtifactShellApp, {
    innerDocUrl: shellData.innerDocUrl,
    bookmarkId: shellData.bookmarkId ?? null,
    allowedHosts: shellData.allowedHosts ?? {},
}).mount('#app')
