// Bun's test module is available at runtime, outside the production tsconfig.
// @ts-expect-error -- supplied by `bun test`
import { expect, test } from 'bun:test';
import { spawnSync } from 'node:child_process';
import { readFileSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { compile } from 'svelte/compiler';

test('open cell rows react to sorting, weight changes and weight reset', () => {
  const source = readFileSync(
    new URL('../src/lib/components/CellDetails.svelte', import.meta.url),
    'utf8'
  );
  const code = compile(source, { generate: 'client' }).js.code;
  const begin = code.indexOf('{', code.indexOf('export default function')) + 1;
  const end = code.indexOf('\n\tvar section =', begin);
  // Retain the actual compiled instance effects, excluding only DOM rendering.
  // Fail explicitly if a compiler update changes this boundary.
  expect(begin).toBeGreaterThan(0);
  expect(end).toBeGreaterThan(begin);
  const tableCode = compile(
    readFileSync(new URL('../src/lib/components/SpeciesDataTable.svelte', import.meta.url), 'utf8'),
    { generate: 'client' }
  ).js.code;
  const tableBegin = tableCode.indexOf('{', tableCode.indexOf('export default function')) + 1;
  const tableEnd = tableCode.indexOf('\n\tvar div =', tableBegin);
  expect(tableBegin).toBeGreaterThan(0);
  expect(tableEnd).toBeGreaterThan(tableBegin);
  const directory = mkdtempSync(join(tmpdir(), 'ark-cell-details-'));
  const fixture = join(directory, 'effects.ts');
  const resolve = (specifier: string) => JSON.stringify(import.meta.resolve(specifier));
  try {
    writeFileSync(
      fixture,
      `import assert from 'node:assert/strict';
import ${resolve('svelte/internal/flags/legacy')};
import * as $ from ${resolve('svelte/internal/client')};
import { flushSync, tick, onDestroy } from ${JSON.stringify(new URL('src/index-client.js', import.meta.resolve('svelte/package.json')).href)};
import { writable, get } from ${resolve('svelte/store')};
import { weights, resetWeights } from ${JSON.stringify(new URL('../src/lib/stores/weights.ts', import.meta.url).href)};
import { dnaStatus } from ${JSON.stringify(new URL('../src/lib/species-table.ts', import.meta.url).href)};
const cellDetails = writable({resolution:3, species:[
  {species_name:'Synthetic Zeta', family:'Synthetic', redlist_category:'Critically Endangered', dna_level:'Missing Family'},
  {species_name:'Synthetic Alpha', family:'Synthetic', redlist_category:'Endangered', dna_level:'Missing Species'}
]});
const selectedH3 = writable(null), selectedH3Resolution = writable(3);
const resolution = writable(3), habitatSystem = writable(''), cellLoading = writable(false);
const knowledgeLimitsText = () => '', ignoresWindowShortcut = () => false;
let requests = 0;
const api = {cell: () => { requests++; throw new Error('Unexpected fetch'); }};
function instance() {
  let requestOwner = null;
  const $$props = {};
  ${code.slice(begin, end)}
  $.pop();
  return {changeSort, cleanup:$$cleanup,
    rows:()=>$.get(visible).map(r=>[r.species_name,r.priority,r.dnaLabel]),
    state:()=>[$.get(sort),$.get(order)]};
}
let cell;
const dispose = $.effect_root(()=>{cell=instance();});
function tableInstance($$props) {
  ${tableCode.slice(tableBegin, tableEnd)}
  $.pop();
  return {chooseSort};
}
let table;
const disposeTable = $.effect_root(()=>{table=tableInstance({
  get sort(){return cell.state()[0];}, get order(){return cell.state()[1];}, onSort:cell.changeSort
});});
try {
  flushSync();
  assert.deepEqual(cell.rows(), [
    ['Synthetic Zeta',16,'Missing Family (4)'],['Synthetic Alpha',6,'Missing Species (2)']
  ]);
  // Exercise the actual mobile select handler and the shared desktop callback.
  table.chooseSort({currentTarget:{value:'species_name'}}); flushSync();
  assert.deepEqual(cell.state(), ['species_name','asc']);
  assert.equal(cell.rows()[0][0], 'Synthetic Alpha');
  table.chooseSort({currentTarget:{value:'species_name'}}); flushSync();
  assert.deepEqual(cell.state(), ['species_name','asc']);
  cell.changeSort('species_name'); flushSync();
  assert.deepEqual(cell.state(), ['species_name','desc']);
  assert.equal(cell.rows()[0][0], 'Synthetic Zeta');
  cell.changeSort('priority'); flushSync();
  weights.update(w=>({...w,cr:0,fam:1,sp:3})); flushSync();
  assert.deepEqual(cell.rows(), [
    ['Synthetic Alpha',9,'Missing Species (3)'],['Synthetic Zeta',0,'Missing Family (1)']
  ]);
  cell.changeSort('dna_level'); flushSync();
  assert.equal(cell.rows()[0][0], 'Synthetic Alpha');
  cell.changeSort('dna_level'); flushSync();
  assert.equal(cell.rows()[0][0], 'Synthetic Zeta');
  resetWeights(); flushSync();
  assert.equal(get(weights).cr,4);
  assert.deepEqual(cell.rows(), [
    ['Synthetic Alpha',6,'Missing Species (2)'],['Synthetic Zeta',16,'Missing Family (4)']
  ]);
  cell.changeSort('priority'); flushSync();
  assert.equal(cell.rows()[0][0], 'Synthetic Zeta');
  assert.equal(requests,0);
  console.log('PASS compiled cell effects: sort/reverse, scores/DNA labels, weights/reset, no fetch/filter');
} finally {
  disposeTable(); dispose(); cell.cleanup(); resetWeights();
}
`
    );
    // Select Svelte's browser effect runtime without requiring a DOM or browser.
    const result = spawnSync(process.execPath, ['--conditions', 'browser', fixture], {
      encoding: 'utf8'
    });
    expect(result.stderr).toBe('');
    expect(result.status).toBe(0);
    expect(result.stdout).toContain('PASS compiled cell effects');
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
