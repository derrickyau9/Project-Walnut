/* Portable, bounded workflow recipes. Importing never executes a workflow. */
export const RECIPE_FORMAT = 'hasslefree.workflow-recipe.v1';
export const MAX_RECIPE_BYTES = 65536;

function requireValue(ok, message) { if (!ok) throw new Error(message); }
function object(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function exactKeys(value, allowed) {
  requireValue(object(value), 'Recipe fields must be JSON objects.');
  requireValue(Object.keys(value).every(key => allowed.includes(key)), 'Recipe has unsupported fields.');
}

/* A small strict JSON reader retains duplicate-key detection lost by JSON.parse. */
function strictJson(text) {
  let at = 0; let nodes = 0;
  const space = () => { while (/[\t\n\r ]/.test(text[at] || '\u0000')) at += 1; };
  function string() {
    const start = at; requireValue(text[at++] === '"', 'Expected a JSON string.');
    while (at < text.length) {
      const value = text[at++];
      if (value === '"') return JSON.parse(text.slice(start, at));
      if (value === '\\') at += 1;
    }
    throw new Error('Unterminated JSON string.');
  }
  function value(depth = 0) {
    requireValue(depth <= 32 && ++nodes <= 8192, 'Recipe JSON is too complex.');
    space(); const head = text[at];
    if (head === '"') return string();
    if (head === '{') {
      at += 1; const result = Object.create(null); space();
      if (text[at] === '}') { at += 1; return result; }
      while (at < text.length) {
        space(); const key = string();
        requireValue(!Object.hasOwn(result, key), 'Duplicate JSON field.');
        space(); requireValue(text[at++] === ':', 'Expected a JSON field separator.');
        result[key] = value(depth + 1); space();
        if (text[at] === '}') { at += 1; return result; }
        requireValue(text[at++] === ',', 'Expected a JSON object separator.');
      }
      throw new Error('Incomplete JSON object.');
    }
    if (head === '[') {
      at += 1; const result = []; space();
      if (text[at] === ']') { at += 1; return result; }
      while (at < text.length) {
        result.push(value(depth + 1)); space();
        if (text[at] === ']') { at += 1; return result; }
        requireValue(text[at++] === ',', 'Expected a JSON array separator.');
      }
      throw new Error('Incomplete JSON array.');
    }
    for (const [token, result] of [['true', true], ['false', false], ['null', null]]) {
      if (text.startsWith(token, at)) { at += token.length; return result; }
    }
    const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.slice(at));
    requireValue(match !== null, 'Invalid JSON value.'); at += match[0].length;
    const result = Number(match[0]); requireValue(Number.isFinite(result), 'Recipe numbers must be finite.');
    return result;
  }
  const result = value(); space(); requireValue(at === text.length, 'Unexpected data after recipe JSON.'); return result;
}

export function normalizeRecipe(recipe) {
  exactKeys(recipe, ['format', 'name', 'plan']);
  requireValue(recipe.format === RECIPE_FORMAT && typeof recipe.name === 'string' && object(recipe.plan), 'Unsupported recipe format.');
  const name = recipe.name;
  requireValue(name.trim() === name && [...name].length >= 1 && [...name].length <= 80 && [...name].every(char => char === ' ' || !/[\p{C}\p{Z}]/u.test(char)), 'Recipe name must be 1–80 printable characters.');
  const plan = recipe.plan;
  exactKeys(plan, ['kind', 'exposures_us', 'sensor_mode', 'requested_analogue_gain', 'output_format', 'interval_ms']);
  const kind = plan.kind === undefined ? 'bracket' : plan.kind;
  requireValue(['bracket', 'interval', 'dark', 'flat'].includes(kind), 'Unsupported recipe sequence.');
  requireValue(Array.isArray(plan.exposures_us) && plan.exposures_us.length >= 1 && plan.exposures_us.length <= 100 && plan.exposures_us.every(n => Number.isInteger(n) && n >= 100 && n <= 30000000), 'Use 1–100 integer exposures from 100 to 30000000 us.');
  const mode = plan.sensor_mode === undefined ? 'sim-small' : plan.sensor_mode;
  const gain = plan.requested_analogue_gain === undefined ? 1 : plan.requested_analogue_gain;
  const format = plan.output_format === undefined ? 'dng' : plan.output_format;
  requireValue(['sim-small', 'sim-full'].includes(mode) && format === 'dng', 'Unsupported recipe mode or output.');
  requireValue(typeof gain === 'number' && Number.isFinite(gain) && gain >= 1 && gain <= 16, 'Recipe gain must be from 1 to 16.');
  const normalized = { exposures_us: [...plan.exposures_us], sensor_mode: mode, requested_analogue_gain: gain, output_format: 'dng' };
  if (kind !== 'bracket') normalized.kind = kind;
  if (kind === 'interval') {
    requireValue(Number.isInteger(plan.interval_ms) && plan.interval_ms >= 0 && plan.interval_ms <= 86400000, 'Interval must be an integer from 0 to 86400000 ms.');
    normalized.interval_ms = plan.interval_ms;
  } else requireValue(plan.interval_ms === undefined, 'Only interval recipes accept interval_ms.');
  return { format: RECIPE_FORMAT, name, plan: normalized };
}

export function parseRecipe(text) {
  requireValue(typeof text === 'string' && new TextEncoder().encode(text).length <= MAX_RECIPE_BYTES, 'Recipe exceeds 64 KiB.');
  requireValue(!text.startsWith('\ufeff'), 'Recipe must be UTF-8 JSON without a BOM.');
  return normalizeRecipe(strictJson(text));
}

export function serializeRecipe(recipe) {
  const text = JSON.stringify(normalizeRecipe(recipe), null, 2) + '\n';
  requireValue(new TextEncoder().encode(text).length <= MAX_RECIPE_BYTES, 'Recipe exceeds 64 KiB.');
  return text;
}
