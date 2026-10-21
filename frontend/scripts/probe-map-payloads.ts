/** Private cloud payload probe using the same PMTiles and MVT libraries as ARK. */
import { open, stat, writeFile } from 'node:fs/promises';
import { MVTLoader } from '@loaders.gl/mvt';
import { PMTiles, type Source } from 'pmtiles';

const [archivePath, outputPath] = process.argv.slice(2);
if (!archivePath || !outputPath) {
  throw new Error('Provide an existing private archive and a fresh JSON output path');
}
try {
  await stat(outputPath);
  throw new Error('Output already exists');
} catch (error) {
  if (!(error instanceof Error) || !('code' in error) || error.code !== 'ENOENT') {
    throw error;
  }
}

const file = await open(archivePath, 'r');
const trials = [];
try {
  for (const point of [
    { label: 'continental', lat: 55.67, lon: 12.56 },
    { label: 'ocean', lat: -30, lon: -140 }
  ]) {
    // Current coarse archives cover zooms 0–6; fine cells use the API.
    for (const z of [3, 6]) {
      const requests: { offset: number; bytes: number }[] = [];
      const source: Source = {
        getKey: () => archivePath,
        async getBytes(offset, length) {
          const buffer = Buffer.alloc(length);
          const { bytesRead } = await file.read(buffer, 0, length, offset);
          requests.push({ offset, bytes: bytesRead });
          return { data: new Uint8Array(buffer.subarray(0, bytesRead)).buffer };
        }
      };
      const archive = new PMTiles(source);
      const x = Math.floor(((point.lon + 180) / 360) * 2 ** z);
      const y = Math.floor(
        ((1 - Math.asinh(Math.tan((point.lat * Math.PI) / 180)) / Math.PI) / 2) * 2 ** z
      );
      const start = performance.now();
      const tile = await archive.getZxy(z, x, y);
      const loadMs = performance.now() - start;
      const coldRequests = [...requests];
      if (!tile) {
        trials.push({ label: point.label, z, present: false, coldRequests });
        continue;
      }
      const decodeMs = [];
      let features = 0;
      for (let index = 0; index < 3; index++) {
        const before = performance.now();
        const parsed = await MVTLoader.parse(tile.data, {
          mvt: { shape: 'geojson', coordinates: 'local' }
        });
        decodeMs.push(performance.now() - before);
        if (!Array.isArray(parsed)) throw new Error('Expected a GeoJSON feature array');
        features = parsed.length;
      }
      requests.length = 0;
      const warmStart = performance.now();
      await archive.getZxy(z, x, y);
      trials.push({
        label: point.label,
        z,
        present: true,
        features,
        decompressedTileBytes: tile.data.byteLength,
        compressedTileBytes: coldRequests.at(-1)?.bytes,
        coldRangeBytes: coldRequests.reduce((sum, request) => sum + request.bytes, 0),
        coldRangeRequests: coldRequests.length,
        directoryCachedRangeBytes: requests.reduce((sum, request) => sum + request.bytes, 0),
        directoryCachedRangeRequests: requests.length,
        localReadAndDecompressMs: loadMs,
        directoryCachedLocalReadMs: performance.now() - warmStart,
        mvtDecodeMs: decodeMs
      });
    }
  }
  await writeFile(
    outputPath,
    JSON.stringify(
      {
        scope: 'Private saved archive; Bun CPU decode proxy, no browser/network/render measurement',
        archiveBytes: (await file.stat()).size,
        trials
      },
      null,
      2
    ),
    { flag: 'wx' }
  );
} finally {
  await file.close();
}
