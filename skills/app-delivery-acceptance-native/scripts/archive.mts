/** Read and write the Skill's bounded, in-memory evidence ZIP format. */
import { crc32, deflateRawSync, inflateRawSync } from "node:zlib";
import { Blocked, decodeUtf8 } from "./records.mts";

export const ARCHIVE_BYTE_LIMIT = 50 * 1024 * 1024;
const LOCAL = 0x04034b50, CENTRAL = 0x02014b50, END = 0x06054b50;
const UTF8 = 0x0800;

export function writeZip(entries: Map<string, Buffer>) {
  const parts: Buffer[] = [], directory: Buffer[] = [];
  let offset = 0;
  const date = new Date();
  const dosTime = (date.getHours() << 11) | (date.getMinutes() << 5) | (date.getSeconds() >> 1);
  const year = Math.min(2107, Math.max(1980, date.getFullYear()));
  const dosDate = ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
  for (const [name, data] of entries) {
    const filename = Buffer.from(name), packed = deflateRawSync(data), crc = crc32(data);
    if (filename.length > 0xffff) throw new Blocked("archive filename is too long");
    const header = Buffer.alloc(30), index = Buffer.alloc(46);
    header.writeUInt32LE(LOCAL);
    index.writeUInt32LE(CENTRAL);
    index.writeUInt16LE(0x0314, 4);
    header.writeUInt16LE(20, 4);
    header.writeUInt16LE(UTF8, 6);
    header.writeUInt16LE(8, 8);
    header.writeUInt16LE(dosTime, 10);
    header.writeUInt16LE(dosDate, 12);
    header.writeUInt32LE(crc, 14);
    header.writeUInt32LE(packed.length, 18);
    header.writeUInt32LE(data.length, 22);
    header.writeUInt16LE(filename.length, 26);
    header.copy(index, 6, 4, 30);
    index.writeUInt32LE(0x01800000, 38); // Private POSIX file permissions.
    index.writeUInt32LE(offset, 42);
    parts.push(header, filename, packed);
    directory.push(index, filename);
    offset += header.length + filename.length + packed.length;
  }
  const centralSize = directory.reduce((size, part) => size + part.length, 0), end = Buffer.alloc(22);
  const count = Math.min(entries.size, 0xffff);
  end.writeUInt32LE(END);
  end.writeUInt16LE(count, 8);
  end.writeUInt16LE(count, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(offset, 16);
  if (entries.size >= 0xffff) {
    const record = Buffer.alloc(56), locator = Buffer.alloc(20);
    record.writeUInt32LE(0x06064b50);
    record.writeBigUInt64LE(44n, 4);
    record.writeUInt16LE(45, 12);
    record.writeUInt16LE(45, 14);
    record.writeBigUInt64LE(BigInt(entries.size), 24);
    record.writeBigUInt64LE(BigInt(entries.size), 32);
    record.writeBigUInt64LE(BigInt(centralSize), 40);
    record.writeBigUInt64LE(BigInt(offset), 48);
    locator.writeUInt32LE(0x07064b50);
    locator.writeBigUInt64LE(BigInt(offset + centralSize), 8);
    locator.writeUInt32LE(1, 16);
    directory.push(record, locator);
  }
  return Buffer.concat([...parts, ...directory, end]);
}

export function readZip(archive: Buffer) {
  const invalid = () => new Blocked("invalid acceptance ZIP archive");
  let end = archive.length - 22;
  while (end >= Math.max(0, archive.length - 22 - 0xffff)) {
    if (archive.readUInt32LE(end) === END && end + 22 + archive.readUInt16LE(end + 20) === archive.length) break;
    end--;
  }
  if (end < Math.max(0, archive.length - 22 - 0xffff)) throw invalid();
  if (archive.readUInt16LE(end + 4) || archive.readUInt16LE(end + 6)) throw invalid();
  const offset = archive.readUInt32LE(end + 16), size = archive.readUInt32LE(end + 12);
  const directoryEnd = offset + size;
  if (directoryEnd > end) throw invalid();
  const entries = new Map<string, { method: number; crc: number; packed: number; size: number; start: number }>();
  for (let cursor = offset; cursor < directoryEnd;) {
    if (cursor + 46 > directoryEnd || archive.readUInt32LE(cursor) !== CENTRAL) throw invalid();
    const nameLength = archive.readUInt16LE(cursor + 28);
    const next = cursor + 46 + nameLength + archive.readUInt16LE(cursor + 30) + archive.readUInt16LE(cursor + 32);
    const start = archive.readUInt32LE(cursor + 42), flags = archive.readUInt16LE(cursor + 8);
    if (next > directoryEnd || start + 30 > offset || archive.readUInt32LE(start) !== LOCAL || flags & 1) throw invalid();
    const name = decodeUtf8(archive.subarray(cursor + 46, cursor + 46 + nameLength));
    const localName = start + 30 + archive.readUInt16LE(start + 26);
    if (localName > offset || !archive.subarray(start + 30, localName).equals(archive.subarray(cursor + 46, cursor + 46 + nameLength))) {
      throw invalid();
    }
    const dataStart = localName + archive.readUInt16LE(start + 28);
    const packed = archive.readUInt32LE(cursor + 20), length = archive.readUInt32LE(cursor + 24);
    if (entries.has(name) || dataStart + packed > offset) throw invalid();
    entries.set(name, { method: archive.readUInt16LE(cursor + 10), crc: archive.readUInt32LE(cursor + 16),
      packed, size: length, start: dataStart });
    cursor = next;
  }
  return (name: string) => {
    const entry = entries.get(name);
    if (!entry || entry.size > ARCHIVE_BYTE_LIMIT) throw invalid();
    const raw = archive.subarray(entry.start, entry.start + entry.packed);
    const data = entry.method === 8 ? inflateRawSync(raw, { maxOutputLength: Math.max(1, entry.size) }) :
      entry.method === 0 ? raw : null;
    if (!data || data.length !== entry.size || crc32(data) !== entry.crc) throw invalid();
    return data;
  };
}
