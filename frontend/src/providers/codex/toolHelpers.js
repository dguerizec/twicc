/**
 * Tool-rendering helpers for Codex sessions.
 *
 * First pass: every behaviour is inherited from ``BaseToolHelpers``, which
 * means the generic shell falls back to ``JsonHumanView`` for both input
 * and result, skips the summary-description block, and surfaces nothing
 * specialised on errors. Per-tool customisation (shell command formatting,
 * apply_patch diff rendering, exec_command_end enrichment, …) will be
 * added in subsequent passes.
 */

import { PROVIDER } from '../../constants'
import { BaseToolHelpers } from '../baseHelpers'

export class CodexToolHelpers extends BaseToolHelpers {
    static provider = PROVIDER.CODEX
}

export const codexToolHelpers = new CodexToolHelpers()
