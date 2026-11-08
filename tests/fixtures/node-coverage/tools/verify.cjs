const fs = require("node:fs");
const [library, report, product, line] = process.argv.slice(2);
const { createCoverageMap } = require(library);
const map = createCoverageMap(JSON.parse(fs.readFileSync(report, "utf8")));
const file = map.fileCoverageFor(product);
const counts = file.getLineCoverage();
console.log(JSON.stringify({
  observed: counts[line] > 0,
  metrics: map.getCoverageSummary().toJSON(),
}));
