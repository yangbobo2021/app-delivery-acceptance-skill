/** Match the historic unified diff for candidate document backfills. */
type Block = [start: number, otherStart: number, length: number];
type Opcode = [kind: "equal" | "insert" | "delete" | "replace", a: number, aEnd: number, b: number, bEnd: number];

function lines(text: string) {
  return text.match(/[^\r\n\v\f\x1c-\x1e\x85\u2028\u2029]*(?:\r\n|[\r\n\v\f\x1c-\x1e\x85\u2028\u2029])|[^\r\n\v\f\x1c-\x1e\x85\u2028\u2029]+$/gu) ?? [];
}
function matching(a: string[], b: string[]): Block[] {
  const byLine = new Map<string, number[]>();
  for (const [i, line] of b.entries()) {
    if (!byLine.has(line)) byLine.set(line, []);
    byLine.get(line)!.push(i);
  }
  if (b.length >= 200) {
    for (const [line, positions] of byLine) if (positions.length > Math.floor(b.length / 100) + 1) byLine.delete(line);
  }
  const pending: number[][] = [[0, a.length, 0, b.length]], found: Block[] = [];
  while (pending.length) {
    const [aLo, aHi, bLo, bHi] = pending.pop()!;
    let start = aLo, other = bLo, length = 0, previous = new Map<number, number>();
    for (let i = aLo; i < aHi; i++) {
      const next = new Map<number, number>();
      for (const j of byLine.get(a[i]) ?? []) {
        if (j < bLo) continue;
        if (j >= bHi) break;
        const n = (previous.get(j - 1) ?? 0) + 1;
        next.set(j, n);
        if (n > length) { start = i - n + 1; other = j - n + 1; length = n; }
      }
      previous = next;
    }
    while (start > aLo && other > bLo && a[start - 1] === b[other - 1]) { start--; other--; length++; }
    while (start + length < aHi && other + length < bHi && a[start + length] === b[other + length]) length++;
    if (!length) continue;
    found.push([start, other, length]);
    if (aLo < start && bLo < other) pending.push([aLo, start, bLo, other]);
    if (start + length < aHi && other + length < bHi) pending.push([start + length, aHi, other + length, bHi]);
  }
  const merged: Block[] = [];
  for (const block of found.sort(([a, b], [c, d]) => a - c || b - d)) {
    const last = merged.at(-1);
    if (last && last[0] + last[2] === block[0] && last[1] + last[2] === block[1]) last[2] += block[2];
    else merged.push(block);
  }
  return [...merged, [a.length, b.length, 0]];
}
function* groups(codes: Opcode[], context = 3): Generator<Opcode[]> {
  const first = codes[0], last = codes.at(-1);
  if (first?.[0] === "equal") {
    first[1] = Math.max(first[1], first[2] - context);
    first[3] = Math.max(first[3], first[4] - context);
  }
  if (last?.[0] === "equal") {
    last[2] = Math.min(last[2], last[1] + context);
    last[4] = Math.min(last[4], last[3] + context);
  }
  let group: Opcode[] = [];
  for (const [kind, a, aEnd, b, bEnd] of codes) {
    if (kind === "equal" && aEnd - a > context * 2) {
      group.push([kind, a, a + context, b, b + context]);
      yield group;
      group = [[kind, aEnd - context, aEnd, bEnd - context, bEnd]];
    } else group.push([kind, a, aEnd, b, bEnd]);
  }
  if (group.length && (group.length > 1 || group[0][0] !== "equal")) yield group;
}
function range(start: number, end: number) {
  const length = end - start;
  return length === 1 ? String(start + 1) : String(start + Number(length > 0)) + "," + length;
}
export function documentDiff(before: string, after: string, name: string) {
  const a = lines(before), b = lines(after), codes: Opcode[] = [];
  let aEnd = 0, bEnd = 0;
  for (const [i, j, length] of matching(a, b)) {
    if (aEnd < i || bEnd < j) codes.push([aEnd < i && bEnd < j ? "replace" : aEnd < i ? "delete" : "insert", aEnd, i, bEnd, j]);
    aEnd = i + length;
    bEnd = j + length;
    if (length) codes.push(["equal", i, aEnd, j, bEnd]);
  }
  const output: string[] = [];
  for (const group of groups(codes)) {
    if (!output.length) output.push("--- a/" + name + "\n", "+++ b/" + name + "\n");
    const first = group[0], last = group.at(-1)!;
    output.push("@@ -" + range(first[1], last[2]) + " +" + range(first[3], last[4]) + " @@\n");
    for (const [kind, i, j, k, l] of group) {
      if (kind === "equal") output.push(...a.slice(i, j).map(line => " " + line));
      if (kind === "replace" || kind === "delete") output.push(...a.slice(i, j).map(line => "-" + line));
      if (kind === "replace" || kind === "insert") output.push(...b.slice(k, l).map(line => "+" + line));
    }
  }
  return output.join("");
}
