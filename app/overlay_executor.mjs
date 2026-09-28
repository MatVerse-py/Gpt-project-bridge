import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

// Exact text contract: CRLF and CR become LF; no Unicode or whitespace folding.
const input = JSON.parse(readFileSync(0, 'utf8'));
if (typeof input.text !== 'string') throw new Error('text_required');
const normalized = input.text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
const bytes = Buffer.from(normalized, 'utf8');
process.stdout.write(JSON.stringify({
  text: normalized,
  sha256: createHash('sha256').update(bytes).digest('hex'),
  utf8_bytes: bytes.length,
}));
