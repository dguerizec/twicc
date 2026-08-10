// frontend/src/utils/codeCommentsStorage.js
// IndexedDB CRUD for code comments (inline annotations on code lines).
// Uses the shared 'twicc' database managed by draftStorage.js.

import { getDb, CODE_COMMENTS_STORE } from './draftStorage.js'

/**
 * Save (create or update) a code comment.
 * The store uses a compound keyPath, so the key is extracted from the object automatically.
 * @param {Object} comment - The comment data object (must include all keyPath fields)
 * @returns {Promise<void>}
 */
export async function saveCodeComment(comment) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(CODE_COMMENTS_STORE, 'readwrite')
        const store = tx.objectStore(CODE_COMMENTS_STORE)
        const request = store.put(comment)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Delete a code comment by compound key array.
 * @param {Array} keyArray - [projectId, sessionId, filePath, source, sourceRef, lineNumber]
 * @returns {Promise<void>}
 */
export async function deleteCodeComment(keyArray) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(CODE_COMMENTS_STORE, 'readwrite')
        const store = tx.objectStore(CODE_COMMENTS_STORE)
        const request = store.delete(keyArray)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Get all code comments (used at app startup to hydrate the store).
 * @returns {Promise<Array>} Array of comment data objects
 */
export async function getAllCodeComments() {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(CODE_COMMENTS_STORE, 'readonly')
        const store = tx.objectStore(CODE_COMMENTS_STORE)
        const request = store.getAll()
        request.onsuccess = () => resolve(request.result || [])
        request.onerror = () => reject(request.error)
    })
}
