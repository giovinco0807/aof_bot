/*
 * Does the OFC reader decode a packet correctly when the offsets are not the
 * ones it shipped with?
 *
 * A fake IL2CPP heap is built here with field offsets deliberately different
 * from the Windows dump the schema carries as a fallback. If the reader were
 * quietly using those fallbacks it would read the wrong bytes and this would
 * fail, so a pass means the metadata path is genuinely doing the work --
 * which is the whole reason the reader exists.
 *
 * Run by ofc/tests/test_ofc.py when node is on PATH; skipped when it is not.
 * Standalone:  node ofc/tests/hook_android_test.js
 */
const fs = require("fs");
const HEAP = Buffer.alloc(0x4000);
let bump = 0x100;
const alloc = (n) => { const p = bump; bump += n; bump = (bump + 15) & ~15; return p; };

// --- Frida API の最小モック ---
class Ptr {
  constructor(a) { this.a = a; }
  add(n) { return new Ptr(this.a + (n instanceof Ptr ? n.a : n)); }
  sub(n) { return new Ptr(this.a - (n instanceof Ptr ? n.a : n)); }
  isNull() { return this.a === 0; }
  readPointer() { return new Ptr(Number(HEAP.readBigUInt64LE(this.a))); }
  readS32() { return HEAP.readInt32LE(this.a); }
  readU8() { return HEAP.readUInt8(this.a); }
  readS64() { const v = HEAP.readBigInt64LE(this.a); return { toNumber: () => Number(v) }; }
  readUtf8String() { let e = this.a; while (HEAP[e] !== 0) e++; return HEAP.toString("utf8", this.a, e); }
  readUtf16String(len) { return HEAP.toString("utf16le", this.a, this.a + len * 2); }
  readULong() { return Number(HEAP.readBigUInt64LE(this.a)); }
  toString() { return "0x" + this.a.toString(16); }
}
const writeP = (at, p) => HEAP.writeBigUInt64LE(BigInt(p), at);

// クラス: 名前 + フィールド名->オフセット
const CLASSES = new Map();
function makeClass(name, fields) {
  const p = alloc(8);
  CLASSES.set(p, { name, fields });
  return p;
}

global.Process = { pointerSize: 8, getModuleByName: () => MODULE };
const MODULE = {
  name: "libil2cpp.so", base: new Ptr(0),
  findExportByName: (n) => (EXPORTS[n] !== undefined ? n : null),
};
const EXPORTS = {};
["il2cpp_domain_get","il2cpp_domain_get_assemblies","il2cpp_assembly_get_image",
 "il2cpp_image_get_class_count","il2cpp_image_get_class","il2cpp_class_get_name",
 "il2cpp_class_get_method_from_name","il2cpp_class_get_field_from_name",
 "il2cpp_field_get_offset","il2cpp_object_get_class","il2cpp_string_chars",
 "il2cpp_string_length"].forEach(n => EXPORTS[n] = true);

const OBJ_CLASS = new Map();   // object addr -> class addr
const STRINGS = new Map();     // string addr -> js string

global.NativeFunction = function (name) {
  const impl = {
    il2cpp_domain_get: () => new Ptr(1),
    il2cpp_domain_get_assemblies: (_d, countPtr) => { HEAP.writeBigUInt64LE(1n, countPtr.a); return ASSEMBLIES; },
    il2cpp_assembly_get_image: () => new Ptr(2),
    il2cpp_image_get_class_count: () => ALL_CLASSES.length,
    il2cpp_image_get_class: (_i, idx) => new Ptr(ALL_CLASSES[idx]),
    il2cpp_class_get_name: (k) => { const c = CLASSES.get(k.a); const p = alloc(64); HEAP.write(c.name + "\0", p); return new Ptr(p); },
    il2cpp_class_get_method_from_name: (k, nameP, argc) => {
      const want = nameP.readUtf8String();
      if (want === "OnDispatchPacket" && argc === 2 && CLASSES.get(k.a).name === "Network") {
        const mi = alloc(16); writeP(mi, 0xDEAD00); return new Ptr(mi);
      }
      return new Ptr(0);
    },
    il2cpp_class_get_field_from_name: (k, nameP) => {
      const c = CLASSES.get(k.a); const want = nameP.readUtf8String();
      if (c && c.fields && want in c.fields) { const f = alloc(8); FIELDS.set(f, c.fields[want]); return new Ptr(f); }
      return new Ptr(0);
    },
    il2cpp_field_get_offset: (f) => FIELDS.get(f.a),
    il2cpp_object_get_class: (o) => new Ptr(OBJ_CLASS.get(o.a) || 0),
    il2cpp_string_chars: (s) => new Ptr(s.a + 0x14),
    il2cpp_string_length: (s) => STRINGS.get(s.a).length,
  }[name];
  return impl;
};
const FIELDS = new Map();
global.Memory = {
  alloc: (n) => new Ptr(alloc(n)),
  allocUtf8String: (s) => { const p = alloc(s.length + 1); HEAP.write(s + "\0", p); return new Ptr(p); },
};
const ATTACHED = [];
global.Interceptor = { attach: (p, h) => ATTACHED.push({ p, h }) };
const SENT = [];
global.send = (m) => SENT.push(m);
global.setTimeout = () => {};

// --- 実際のオブジェクトを組む ---
function mkString(s) {
  const p = alloc(0x14 + s.length * 2 + 2);
  HEAP.write(s, p + 0x14, "utf16le");
  STRINGS.set(p, s);
  return p;
}
function mkIntList(values) {
  const arr = alloc(0x20 + values.length * 4);
  values.forEach((v, i) => HEAP.writeInt32LE(v, arr + 0x20 + i * 4));
  const list = alloc(0x20);
  writeP(list + 0x10, arr); HEAP.writeInt32LE(values.length, list + 0x18);
  return list;
}
function mkObjList(ptrs) {
  const arr = alloc(0x20 + ptrs.length * 8);
  ptrs.forEach((p, i) => writeP(arr + 0x20 + i * 8, p));
  const list = alloc(0x20);
  writeP(list + 0x10, arr); HEAP.writeInt32LE(ptrs.length, list + 0x18);
  return list;
}

// Android では別オフセットになる、というのを再現する（Windows dump と意図的にズラす）
const cardCls = makeClass("PineCard", {
  headScore_: 0x30, middleScore_: 0x34, tailScore_: 0x38,
  headCard_: 0x40, middleCard_: 0x48, tailCard_: 0x50, abandonCard_: 0x58,
  headType_: 0x60, middleType_: 0x64, tailType_: 0x68, bust_: 0x6C,
  handCard_: 0x70, round_: 0x78,
});
const handCardCls = makeClass("PineHandCard", {
  card_: 0x30, round_: 0x38, fantasy_: 0x3C, uid_: 0x40, seatid_: 0x44, actionLeftTime_: 0x4C,
});
const handBrcCls = makeClass("PineHandCardBRC", { handCard_: 0x30, actionSeatid_: 0x38 });
const statusCls = makeClass("PinePlayingStatus", {
  uid_: 0x30, seatid_: 0x34, card_: 0x38, fantasy_: 0x40, sittingOut_: 0x44,
  actionLeftTime_: 0x48, name_: 0x50, chips_: 0x58, ready_: 0x60,
});
const roomCls = makeClass("PineRoomStatusBRC", { pinePlayingStatus_: 0x30 });
const networkCls = makeClass("Network", {});
const ALL_CLASSES = [cardCls, handCardCls, handBrcCls, statusCls, roomCls, networkCls];
const ASSEMBLIES = new Ptr(alloc(8)); writeP(ASSEMBLIES.a, alloc(8));

// PineCard 一つ
const card = alloc(0x80); OBJ_CLASS.set(card, cardCls);
HEAP.writeInt32LE(6, card + 0x30); HEAP.writeInt32LE(8, card + 0x34); HEAP.writeInt32LE(4, card + 0x38);
writeP(card + 0x40, mkIntList([0x10E, 0x20E, 0x30D]));      // As Ad Kh 相当の wire 値
writeP(card + 0x48, mkIntList([0x407, 0x102]));
writeP(card + 0x50, mkIntList([]));
writeP(card + 0x58, mkIntList([0x205]));
HEAP.writeInt32LE(1, card + 0x60); HEAP.writeInt32LE(2, card + 0x64); HEAP.writeInt32LE(3, card + 0x68);
HEAP.writeUInt8(1, card + 0x6C);
writeP(card + 0x70, mkIntList([0x309]));
HEAP.writeInt32LE(4, card + 0x78);

// PineHandCardBRC
const hc = alloc(0x60); OBJ_CLASS.set(hc, handCardCls);
writeP(hc + 0x30, mkIntList([0x10E, 0x20E, 0x30D, 0x407, 0x102]));
HEAP.writeInt32LE(0, hc + 0x38); HEAP.writeInt32LE(0, hc + 0x3C);
HEAP.writeInt32LE(1001, hc + 0x40); HEAP.writeInt32LE(0, hc + 0x44);
HEAP.writeInt32LE(30, hc + 0x4C);
const hcBrc = alloc(0x40); OBJ_CLASS.set(hcBrc, handBrcCls);
writeP(hcBrc + 0x30, mkObjList([hc])); HEAP.writeInt32LE(0, hcBrc + 0x38);

// PineRoomStatusBRC
const st = alloc(0x70); OBJ_CLASS.set(st, statusCls);
HEAP.writeInt32LE(2002, st + 0x30); HEAP.writeInt32LE(1, st + 0x34);
writeP(st + 0x38, card);
HEAP.writeInt32LE(0, st + 0x40); HEAP.writeUInt8(0, st + 0x44);
HEAP.writeInt32LE(25, st + 0x48);
writeP(st + 0x50, mkString("villain"));
HEAP.writeBigInt64LE(50000n, st + 0x58); HEAP.writeUInt8(1, st + 0x60);
const room = alloc(0x40); OBJ_CLASS.set(room, roomCls);
writeP(room + 0x30, mkObjList([st]));

// --- スクリプトを走らせる ---
eval(fs.readFileSync(require("path").join(__dirname, "..", "hook_android.js"), "utf8"));

console.log("=== 起動時のメッセージ ===");
SENT.forEach(m => console.log("  " + m.type + ": " + m.message));
if (SENT.some(m => m.type === "error")) { console.log("*** インストール失敗"); process.exit(1); }
if (ATTACHED.length !== 1) { console.log("*** フックが1つ張られていない"); process.exit(1); }

SENT.length = 0;
const onEnter = ATTACHED[0].h.onEnter;
onEnter([new Ptr(0), new Ptr(hcBrc), { toInt32: () => 7 }]);
onEnter([new Ptr(0), new Ptr(room), { toInt32: () => 7 }]);
// OFC 以外は無視されること
const otherCls = makeClass("SomeOtherBRC", {});
const other = alloc(0x20); OBJ_CLASS.set(other, otherCls);
onEnter([new Ptr(0), new Ptr(other), { toInt32: () => 7 }]);

const fail = (m) => { console.error("FAIL: " + m); process.exit(1); };

if (SENT.length !== 2) fail("expected 2 packets, got " + SENT.length +
                            " (a non-OFC packet was not ignored?)");

const hand = SENT[0];
if (hand.name !== "PineHandCardBRC") fail("first packet was " + hand.name);
if (hand.tableId !== 7) fail("table id was " + hand.tableId);
const dealt = hand.data.handCards[0];
if (JSON.stringify(dealt.cards) !== JSON.stringify([270, 526, 781, 1031, 258]))
    fail("dealt cards decoded as " + JSON.stringify(dealt.cards));
if (dealt.uid !== 1001) fail("uid decoded as " + dealt.uid);
if (dealt.actionLeftTime !== 30) fail("clock decoded as " + dealt.actionLeftTime);

const status = SENT[1].data.players[0];
if (status.name !== "villain") fail("name decoded as " + status.name);
if (status.chips !== 50000) fail("chips decoded as " + status.chips);
if (status.sittingOut !== false) fail("sittingOut decoded as " + status.sittingOut);
if (status.ready !== true) fail("ready decoded as " + status.ready);

const board = status.card;
if (board.bust !== true) fail("bust decoded as " + board.bust);
if (JSON.stringify(board.headCard) !== JSON.stringify([270, 526, 781]))
    fail("head row decoded as " + JSON.stringify(board.headCard));
if (JSON.stringify(board.tailCard) !== JSON.stringify([]))
    fail("empty row decoded as " + JSON.stringify(board.tailCard));
if (JSON.stringify(board.abandonCard) !== JSON.stringify([517]))
    fail("discard decoded as " + JSON.stringify(board.abandonCard));
if (board.round !== 4) fail("round decoded as " + board.round);

console.log("hook_android.js: decoded 2 packets from offsets it was not given");
