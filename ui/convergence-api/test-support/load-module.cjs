'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');
// Load route/service compositions in one fixture dependency graph. Domain mocks
// stay at source boundaries; extracting a service does not bypass those mocks.
function runSource(filename, globals = {}) {
  const cache = new Map();
  function load(file, root = false) {
    file = path.resolve(file);
    if (cache.has(file)) return cache.get(file).exports;
    const localRequire = createRequire(file);
    const module = root && globals.module ? globals.module : {exports:{}};
    cache.set(file,module);
    const dependency = name => {
      if (name.endsWith('/read-context') || name === './read-context') return localRequire(name);
      if (name.startsWith('../services/') || (file.includes('/services/') && name.startsWith('./'))) {
        return load(localRequire.resolve(name));
      }
      if (globals.require) {
        const supplied = globals.require(name);
        if (supplied !== undefined) return supplied;
      }
      return localRequire(name);
    };
    vm.runInNewContext(fs.readFileSync(file,'utf8'), {...globals,module,require:dependency}, {filename:file});
    return module.exports;
  }
  return load(filename,true);
}
module.exports={runSource};
