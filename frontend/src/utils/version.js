// frontend/src/utils/version.js
//
// Numeric comparison of dotted version strings (e.g. "1.9.2" vs "1.10.0").

/**
 * Compare two dotted version strings by numeric magnitude.
 *
 * Segments are split on "." and compared as integers, so ordering follows
 * semantic-versioning magnitude rather than lexicographic string order — a
 * plain string comparison gets `"1.9.2" >= "1.10.0"` wrong (it returns true,
 * because '9' > '1'). Missing trailing segments count as 0 ("1.9" === "1.9.0");
 * non-numeric segments are coerced to 0 (we only ever compare TwiCC's own
 * numeric PyPI release strings).
 *
 * @param {string} a - First version string.
 * @param {string} b - Second version string.
 * @returns {number} -1 if a < b, 0 if a === b, 1 if a > b.
 */
export function compareVersions(a, b) {
    const pa = String(a).split('.')
    const pb = String(b).split('.')
    const len = Math.max(pa.length, pb.length)
    for (let i = 0; i < len; i++) {
        const na = parseInt(pa[i], 10) || 0
        const nb = parseInt(pb[i], 10) || 0
        if (na > nb) return 1
        if (na < nb) return -1
    }
    return 0
}
