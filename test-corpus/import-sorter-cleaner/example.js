import fs from 'fs';
import path from 'path';
import './polyfills.js';
import { debounce } from 'lodash';

export function readConfig(name) {
  return fs.readFileSync(path.join('.', name), 'utf8');
}
