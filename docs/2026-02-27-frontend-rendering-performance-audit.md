# 🔍 Rapport d'audit de performance de rendu — Frontend TwiCC

## Table des matières
1. [Problèmes critiques](#1-critiques)
2. [Problèmes importants](#2-importants)
3. [Problèmes modérés](#3-modérés)
4. [Problèmes mineurs](#4-mineurs)
5. [Bonnes pratiques constatées](#5-points-positifs)

---

## 1. Problèmes critiques (impact élevé)

### 1.1 🔴 `SessionList.vue` — Timer 1s provoquant des re-rendus globaux

**Fichier:** `SessionList.vue:298-305`

```js
const now = ref(Date.now() / 1000)
durationTimer = setInterval(() => {
    now.value = Date.now() / 1000
}, 1000)
```

**Problème:** Ce `setInterval` met à jour `now` chaque seconde. Comme `now` est utilisé dans `getStateDuration()`, **tous les items visibles du scroller de sessions** sont re-rendus chaque seconde, même si seulement 1-2 sessions ont un processus actif. La majorité du re-rendu est inutile.

**Impact:** Re-rendu complet de la liste de sessions 1×/seconde, en permanence. Le template appelle `getProcessState(session.id)` jusqu'à **~10 fois par item de session** (voir 1.2), ce qui amplifie le coût.

**Suggestion:** Ne faire tourner le timer que s'il existe des processus en état `assistant_turn`, et isoler le composant de durée pour que seuls les sessions concernées re-rendent.

---

### 1.2 🔴 `SessionList.vue` — Appels multiples redondants à `getProcessState()` et `getPendingRequest()` dans le template

**Fichier:** `SessionList.vue:585-648` (template, dans le `v-for`)

Pour **chaque session** dans la liste, le template appelle :
- `getProcessState(session.id)` : **~8 à 10 fois** (lignes 594, 602, 604, 608-609, 615, 617, 620-621, 627-628, 647)
- `store.getPendingRequest(session.id)` : **~4 fois** (lignes 594, 599, 602, 635, 640)
- `store.getProject(session.project_id)` : **2 fois** (ligne 585)

**Problème:** Ces getters Pinia utilisent le pattern `(state) => (id) => ...` (getter qui retourne une fonction). Ce pattern **n'est pas mis en cache par Pinia** — chaque appel exécute la fonction à nouveau. Comme ces getters sont appelés des dizaines de fois par item à chaque re-rendu (et le re-rendu est déclenché chaque seconde par le timer ci-dessus), l'impact est multiplicatif.

**Impact:** Pour 30 sessions visibles × 10 appels × 1/seconde = ~300 exécutions de getters/seconde, la plupart redondantes.

**Suggestion:** Extraire chaque appel dans un computed local ou un `v-memo` pattern, ou mieux : extraire un sous-composant `SessionListItem` qui ferait ces lookups une seule fois.

---

### 1.3 🔴 `getProjectSessions` / `getAllSessions` — Nouvelles listes triées créées à chaque accès

**Fichier:** `data.js:169-184`

```js
getProjectSessions: (state) => (projectId) => {
    return Object.values(state.sessions)
        .filter(s => s.project_id === projectId && !s.parent_session_id)
        .filter(s => oldestMtime == null || s.mtime >= oldestMtime)
        .sort(sessionSortComparator(state.processStates))
},
getAllSessions: (state) => {
    return Object.values(state.sessions)
        .filter(s => !s.parent_session_id)
        .filter(s => oldestMtime == null || s.mtime >= oldestMtime)
        .sort(sessionSortComparator(state.processStates))
},
```

**Problème:** Ces getters retournent une **fonction** (pattern `(state) => (id) => ...`), donc Pinia ne les met **jamais** en cache. À chaque accès — et `SessionList.allSessions` computed les appelle — le code exécute `Object.values()`, double `filter()`, et `sort()` (O(n log n)) sur l'ensemble des sessions. Pire, `sessionSortComparator` dépend de `processStates`, donc le tri est recalculé même si les sessions n'ont pas changé.

**Impact:** Avec plusieurs centaines de sessions, c'est un coût non-négligeable à chaque accès réactif, et cet accès est déclenché à chaque changement dans `sessions` ou `processStates`.

**Suggestion:** Convertir en getters classiques retournant directement un objet (pas une fonction), et utiliser un computed par projectId si nécessaire.

---

### 1.4 ✅ ~~`recomputeVisualItems` — Parsing JSON et reconstruction d'objets fréquents~~

**Résolu** par le lazy parsed content caching (`parsedContent.js`). Les `JSON.parse()` dans le backward walk passent par `getParsedContent()` (cache sur l'item). Le `JSON.stringify()` pour le workingMessage synthétique est remplacé par `setParsedContent()`.

---

### 1.5 🔴 `recomputeAllVisualItems` — Recompute sur TOUTES les sessions

**Fichier:** `data.js:1063-1067`

```js
recomputeAllVisualItems() {
    for (const sessionId of Object.keys(this.sessionItems)) {
        this.recomputeVisualItems(sessionId)
    }
},
```

**Problème:** Appelé quand le display mode change (settings watcher). Si 50 sessions ont leurs items chargés, ça signifie 50× `recomputeVisualItems`. La plupart de ces sessions ne sont pas visibles à ce moment.

**Suggestion:** Invalider un flag et recomputer lazily uniquement quand la session est affichée.

---

## 2. Problèmes importants

### 2.1 ⚪ `computeVisualItems` (`visualItems.js`) — Multi-passes coûteux — **Négligeable**

**Fichier:** `utils/visualItems.js`

En mode conversation, `computeVisualItems` fait :
1. **Passe 1 :** Parcourt tous les items pour trouver `keptAssistant` messages
2. **Passe 2 :** Construit les `blockIds` (Sets et Maps créés)
3. **Passe 3 :** Construit le résultat final

Chaque appel crée de nouvelles structures (`new Set()`, `new Map()`, nouveaux objets pour chaque visual item). C'est un coût O(n) qui s'additionne avec les appels fréquents à `recomputeVisualItems`.

**Reclassé négligeable :** Le coût JS pur (3 × O(n) sur des primitives et Map/Set) est sous la milliseconde pour des milliers d'items. Depuis la stabilisation des références de visual items, ce calcul ne provoque plus de re-renders Vue inutiles — seuls les items réellement modifiés sont re-rendus. Optimiser davantage (append incrémental, splice pour les groupes) ajouterait beaucoup de complexité pour un gain à peine mesurable.

---

### 2.2 ⚪ `positions` computed dans `useVirtualScroll.js` — Recalcul total à chaque changement de hauteur — **Négligeable**

**Fichier:** `useVirtualScroll.js:129-138`

```js
const positions = computed(() => {
    let top = 0
    return items.value.map((item, index) => {
        const key = itemKey(item)
        const height = heightCache.get(key) ?? minItemHeight
        const pos = { index, key, top, height }
        top += height
        return pos
    })
})
```

**Problème:** `heightCache` est un `reactive(new Map())`. À chaque `.set()` sur cette Map (c'est-à-dire à chaque fois qu'un item est mesuré par le ResizeObserver), Vue invalide le computed `positions`, qui recalcule **toutes** les positions cumulatives de tous les items.

**Impact:** Quand des items sont rendus pour la première fois, le ResizeObserver mesure plusieurs items en rafale. Chaque mesure déclenche un recalcul complet de `positions`. Avec un `batchUpdateItemHeights`, le problème est atténué, mais chaque batch cause quand même un recalcul O(n) complet.

**Reclassé négligeable :** O(n) itérations de `Map.get()` + somme cumulative + création d'objets simples `{ index, key, top, height }` — sous la milliseconde pour des milliers d'items. `batchUpdateItemHeights` regroupe déjà les mesures par callback ResizeObserver. Ce calcul ne provoque pas de re-renders Vue inutiles (il alimente un binary search et le calcul des spacers). Le commentaire dans le code mentionnant des « memoization strategies » peut être retiré.

---

### 2.3 ✅ ~~`SessionItem.vue` — `JSON.parse` dans un computed à chaque render~~

**Résolu** par le lazy parsed content caching. `SessionItem.vue` reçoit maintenant une prop `content` de type Object (déjà parsé via `getParsedContent()`). Plus aucun `JSON.parse` dans le composant.

---

### 2.4 🟠 `getProjects` getter — Tri O(n log n) à chaque accès

**Fichier:** `data.js:167`

```js
getProjects: (state) => Object.values(state.projects).sort((a, b) => b.mtime - a.mtime),
```

**Problème:** Ce getter Pinia retourne directement un résultat (pas une fonction), donc Pinia **le met en cache**. Cependant, il est invalidé à chaque mutation de `state.projects`. Or `updateProject` est appelé fréquemment (WebSocket `project_updated`), et chaque appel invalide ce getter et déclenche un nouveau `Object.values().sort()`.

**Impact modéré** : le nombre de projets est généralement petit (<50), donc le tri est rapide. Mais c'est un pattern à surveiller.

---

## 3. Problèmes modérés

### 3.1 🟡 Settings store — Watcher `{ deep: true }` sur objet reconstruit

**Fichier:** `settings.js:444-473`

```js
watch(
    () => ({
        displayMode: store.displayMode,
        fontSize: store.fontSize,
        // ... 20+ propriétés
    }),
    (newSettings) => { saveSettings(newSettings) },
    { deep: true }
)
```

**Problème:** Le source du watch crée un **nouvel objet** à chaque évaluation (à chaque changement de n'importe quelle propriété du store). Le `{ deep: true }` est techniquement inutile ici puisque toutes les propriétés sont des primitives (strings, booleans, numbers), et le watch se déclenchera déjà car la référence de l'objet change.

**Impact:** Faible en pratique (les settings changent rarement), mais c'est un anti-pattern. Le `deep: true` ajoute un traversal récursif inutile de l'objet.

---

### 3.2 ✅ ~~`VirtualScroller.vue` — `renderedItems` computed crée de nouveaux objets à chaque changement de range~~

**Résolu** par la stabilisation des références de visual items (`recomputeVisualItems`). Les wrappers `{ item, index, key }` sont toujours recréés, mais l'`item` qu'ils contiennent est une référence stabilisée — Vue constate que les props du `SessionItem` n'ont pas changé et skip le re-rendu.

---

### 3.3 🟡 `ContentList.vue` — `expandedInternalGroups` crée un `new Set()` à chaque accès

**Fichier:** `ContentList.vue`

Le computed `expandedInternalGroups` crée un `new Set()` basé sur les données du store à chaque évaluation. Comme il est utilisé dans le template pour chaque groupe interne, cela peut s'accumuler.

---

### 3.4 ⚪ `SessionItemsList.vue` — Inline arrow function dans le template — **Négligeable**

**Fichier:** `SessionItemsList.vue`

```html
:item-key="item => item.lineNum"
```

**Problème:** Cette arrow function inline crée une **nouvelle référence de fonction** à chaque re-rendu du composant parent. Comme `VirtualScroller` reçoit une nouvelle prop `itemKey`, cela pourrait invalider des computeds internes du virtual scroller qui dépendent de `itemKey`.

**Reclassé négligeable :** `useVirtualScroll` capture `itemKey` une seule fois à l'initialisation (pas via un ref). La nouvelle référence de fonction sur la prop est ignorée par le composable — impact zéro.

---

### 3.5 ⚪ `ToolUseContent.vue` — Timers de polling multiples — **Négligeable**

**Fichier:** `ToolUseContent.vue`

Ce composant utilise des `setInterval` pour :
1. Poller les résultats d'outils (tool_result)
2. Poller les liens d'agents (agent_link)

**Reclassé négligeable :** Le scénario « 20 timers simultanés » est irréaliste. Le polling ne se déclenche que pour des résultats pendants (conversation active), uniquement sur des `wa-details` ouverts (lazy rendering), et s'arrête dès que le résultat arrive. L'agent link polling est plafonné à 10 tentatives. Le composant gère correctement les pauses via KeepAlive. En navigation sur des sessions historiques, aucun polling n'est actif.

---

### 3.6 ✅ ~~`setProcessState` dans data store — Déclenche `recomputeVisualItems` à chaque changement d'état~~

**Résolu** par un guard `isAssistantTurn` dans `setProcessState`. Le recompute n'est déclenché que lorsque le booléen `isAssistantTurn` change réellement (entrée/sortie de `ASSISTANT_TURN`), pas sur chaque transition d'état ou mise à jour de `pending_request`.

---

## 4. Problèmes mineurs

### 4.1 🔵 `notifyProcessStateChange` instancie `useSettingsStore()` à chaque appel

**Fichier:** `useWebSocket.js:147`

```js
function notifyProcessStateChange(msg, previousState, route) {
    const settings = useSettingsStore()
    // ...
}
```

Appeler `useSettingsStore()` dans une fonction non-composable est un anti-pattern Vue, mais Pinia le gère grâce au singleton pattern. L'impact est négligeable (simple lookup). Il serait plus propre de le cacher au niveau module.

---

### 4.2 🔵 `SessionList.vue` — `getSessionDisplayName` est une fonction normale, pas un computed

C'est appelé dans le template via `{{ getSessionDisplayName(session) }}`, ce qui est réévalué à chaque re-rendu. L'impact est négligeable car c'est une opération triviale.

---

### 4.3 🔵 `getProjectDisplayName` getter — Mutation dans un getter

**Fichier:** `data.js:349-377`

```js
getProjectDisplayName: (state) => (projectId) => {
    // ...
    // Cache it
    state.localState.projectDisplayNames[projectId] = displayName
    return displayName
}
```

Ce getter **mute le state** en écrivant dans le cache. C'est un anti-pattern Pinia (les getters devraient être purs). En pratique, ça fonctionne mais peut causer des boucles de réactivité dans certains cas.

---

### 4.4 🔵 `App.vue` — `toastTheme` computed crée un nouvel objet

```js
const toastTheme = computed(() => ({
    '--toastify-color-light': '...',
    // ...
}))
```

Crée un nouvel objet à chaque changement de thème. Impact quasi nul car le thème change rarement.

---

## 5. Bonnes pratiques constatées ✅

Le code contient aussi plusieurs excellents patterns de performance qu'il faut souligner :

1. **Shared ResizeObserver** dans `VirtualScroller` — Un seul observer pour tous les items au lieu d'un par item. Excellent.

2. **Hysteresis dans le render range** — Buffers asymétriques load/unload pour éviter le thrashing. Très bien pensé.

3. **RAF-throttled scroll handler** — `requestAnimationFrame` pour lisser les événements de scroll.

4. **Anchor-based scroll preservation** — Sauvegarde d'ancrage par item+offset au lieu du scrollTop brut. Robuste.

5. **KeepAlive lifecycle management** — Suspend/resume du virtual scroller avec `VirtualScroller` : empêche la corruption du scrollTop.

6. **`$patch` avec merge profond** — Utilisation de `this.$patch({ projects: { [id]: project } })` pour ne déclencher les re-rendus que sur les propriétés modifiées.

7. **Batch height updates** — `batchUpdateItemHeights` dans le virtual scroller regroupe les mises à jour de hauteur en un seul update réactif.

8. **Debounced draft notifications** — 10s debounce pour les notifications de draft, évitant le spam WebSocket.

9. **Lazy imports** pour éviter les dépendances circulaires — Pattern cohérent et bien documenté.

---

## Résumé par priorité

| Priorité | Problème | Impact estimé |
|----------|----------|---------------|
| 🔴 Critique | Timer 1s dans SessionList | Re-rendu global chaque seconde |
| 🔴 Critique | Appels multiples getProcessState/getPendingRequest par session item dans template | ×10 par item × 30 visibles × 1/s |
| 🔴 Critique | getProjectSessions/getAllSessions non-cachés | O(n log n) à chaque accès réactif |
| ✅ ~~Critique~~ | ~~recomputeVisualItems avec JSON.parse/stringify fréquent~~ | ~~Parsing coûteux à chaque WS message~~ |
| 🔴 Critique | recomputeAllVisualItems sur toutes les sessions | N × recompute au changement de mode |
| ⚪ ~~Important~~ | computeVisualItems multi-passes avec allocations | Négligeable (JS pur, sous 1ms) |
| ⚪ ~~Important~~ | positions computed recalculé intégralement | Négligeable (JS pur, sous 1ms) |
| ✅ ~~Important~~ | ~~SessionItem JSON.parse dans computed~~ | ~~Parsing de gros JSON par item~~ |
| 🟠 Important | getProjects tri à chaque invalidation | O(n log n) fréquent |
| 🟡 Modéré | Settings watcher deep:true inutile | Traversal récursif superflu |
| ✅ ~~Modéré~~ | ~~VirtualScroller renderedItems nouvelles refs~~ | ~~Possible re-rendu de slots~~ |
| 🟡 Modéré | ContentList new Set() dans computed | Allocation par évaluation |
| ⚪ ~~Modéré~~ | Inline arrow function pour itemKey | Négligeable (capturé au mount) |
| ⚪ ~~Modéré~~ | ToolUseContent timers multiples | Négligeable (auto-limité, lazy) |
| ✅ ~~Modéré~~ | ~~setProcessState → recomputeVisualItems systématique~~ | ~~Recompute superflu fréquent~~ |

---

Le problème le plus impactant est probablement la **combinaison** du timer 1s dans `SessionList` + les appels redondants de getters non-cachés dans le template, car cela crée un "battement de cœur" de re-rendus qui se propage à travers toute la liste de sessions chaque seconde, même quand rien ne change visuellement.
