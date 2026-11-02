// Entry point so that `node --test ui/test` works: Node 22 resolves a directory
// argument as a module path (ui/test/index.js) instead of scanning it.
import('./panel.test.mjs');
