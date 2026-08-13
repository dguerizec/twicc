import assert from 'node:assert/strict'
import test from 'node:test'

import * as computePendingHint from './computePendingHint.js'


function createFakeTimers() {
    let nextId = 1
    const timers = new Map()
    const cleared = []

    return {
        setTimer(fn, timeout) {
            const id = nextId++
            timers.set(id, { fn, timeout })
            return id
        },
        clearTimer(id) {
            cleared.push(id)
            timers.delete(id)
        },
        fireDelay(timeout) {
            const entry = [...timers.entries()].find(([, timer]) => timer.timeout === timeout)
            assert.ok(entry, `No timer found for ${timeout}ms`)
            const [id, timer] = entry
            timers.delete(id)
            timer.fn()
        },
        fireAll() {
            const callbacks = [...timers.values()].map(timer => timer.fn)
            timers.clear()
            callbacks.forEach(fn => fn())
        },
        get delays() {
            return [...timers.values()].map(timer => timer.timeout)
        },
        get clearedCount() {
            return cleared.length
        },
    }
}


test('shows automatic recovery guidance before the later restart guidance', () => {
    const timers = createFakeTimers()
    const phases = []
    const hint = computePendingHint.createComputePendingHint({
        setPhase: phase => phases.push(phase),
        setTimer: timers.setTimer,
        clearTimer: timers.clearTimer,
    })

    hint.update(true)

    assert.deepEqual(phases, [null])
    assert.deepEqual(timers.delays, [2 * 60 * 1000, 10 * 60 * 1000])

    timers.fireDelay(2 * 60 * 1000)

    assert.deepEqual(phases, [null, 'recovery'])

    timers.fireDelay(10 * 60 * 1000)

    assert.deepEqual(phases, [null, 'recovery', 'restart'])
})


test('clears delayed guidance when computation completes', () => {
    const timers = createFakeTimers()
    const phases = []
    const hint = computePendingHint.createComputePendingHint({
        setPhase: phase => phases.push(phase),
        setTimer: timers.setTimer,
        clearTimer: timers.clearTimer,
    })

    hint.update(true)
    hint.update(false)
    timers.fireAll()

    assert.deepEqual(phases, [null, null])
    assert.equal(timers.clearedCount, 2)
})
