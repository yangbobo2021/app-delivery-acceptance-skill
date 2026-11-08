const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const output = execFileSync(process.execPath, ["app/product.cjs", "open"], { encoding: "utf8" });
assert.equal(output.trim(), "reserved");
