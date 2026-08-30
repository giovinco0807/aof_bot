/*
 * OFC packet reader that finds its own way around.
 *
 * The hook this repository already ships resolves Network.OnDispatchPacket by
 * a fixed offset dumped from one Windows build of the client. That is fine
 * until the client updates, and useless on Android, where the same C# is
 * compiled to different code at different addresses inside libil2cpp.so
 * rather than GameAssembly.dll.
 *
 * This one asks IL2CPP instead. The runtime keeps the metadata the C# had --
 * class names, method names, field names and their offsets -- and exports
 * functions to walk it. Nothing here is a magic number that has to be
 * re-derived when something moves.
 *
 * Deliberately OFC only. hook/pppoker_hook.js stays exactly as it is, because
 * the AoF bot depends on it; this is a separate reader for a separate game.
 *
 * The message it sends is byte-for-byte the shape ofc/capture.py already
 * consumes, so nothing on the Python side changes:
 *
 *     { type: "packet", name: "PineHandCardBRC", tableId: 3, data: {...} }
 *
 * On load it reports what it resolved. Run it once and read that report
 * before trusting anything downstream of it -- it is the difference between
 * "attached" and "attached to the right thing".
 */

"use strict";

// The IL2CPP runtime, wherever it lives. Both names are tried so the same
// script can be proven on the Windows client -- where there is already a
// working setup to compare against -- before it is trusted on a phone.
var MODULE_NAMES = ["libil2cpp.so", "GameAssembly.dll"];

var TARGET_CLASS = "Network";
var TARGET_METHOD = "OnDispatchPacket";
var TARGET_ARGC = 2;                       // (IMessage packet, int tableId)

var OFC_PACKETS = [
    "PineHandCardBRC", "PineActionBRC", "PineResultBRC", "PineGameStartBRC",
    "PineSitDownBRC", "PineStandUpBRC", "PineRoomStatusBRC",
];

/*
 * What each packet and nested type is made of.
 *
 *     field: [csharpName, fallbackOffset, kind]
 *
 * The name is what gets used: it is asked of the runtime and answered with
 * whatever offset this build actually has. The number beside it is the
 * offset the Windows dump had, kept only as a fallback for a field the
 * metadata will not name -- which should not happen, and is reported when it
 * does rather than being used silently.
 */
var SCHEMA = {
    PineHandCardBRC: {
        handCards: ["handCard_", 0x20, "list:PineHandCard"],
        actionSeatId: ["actionSeatid_", 0x28, "i32"],
    },
    PineActionBRC: {
        uid: ["uid_", 0x1C, "i32"],
        seatId: ["seatid_", 0x20, "i32"],
        card: ["card_", 0x28, "obj:PineCard"],
    },
    PineResultBRC: {
        playerResults: ["playerResult_", 0x18, "list:PinePlayerResult"],
    },
    PineGameStartBRC: {
        dealerSeatId: ["dealerSeatid_", 0x1C, "i32"],
        leftTime: ["leftTime_", 0x20, "i32"],
        startInfo: ["startInfo_", 0x28, "list:PineGameStartInfo"],
        actionType: ["actionType_", 0x30, "i32"],
        gameId: ["gameid_", 0x38, "str"],
    },
    PineSitDownBRC: {
        player: ["pinePlayingStatus_", 0x18, "obj:PinePlayingStatus"],
    },
    PineStandUpBRC: {
        seatId: ["seatid_", 0x1C, "i32"],
        code: ["code_", 0x20, "i32"],
    },
    PineRoomStatusBRC: {
        players: ["pinePlayingStatus_", 0x18, "list:PinePlayingStatus"],
    },

    PineHandCard: {
        cards: ["card_", 0x20, "ints"],
        round: ["round_", 0x28, "i32"],
        fantasy: ["fantasy_", 0x2C, "i32"],
        uid: ["uid_", 0x30, "i32"],
        seatId: ["seatid_", 0x34, "i32"],
        actionLeftTime: ["actionLeftTime_", 0x3C, "i32"],
    },
    PineCard: {
        headScore: ["headScore_", 0x1C, "i32"],
        middleScore: ["middleScore_", 0x20, "i32"],
        tailScore: ["tailScore_", 0x24, "i32"],
        headCard: ["headCard_", 0x28, "ints"],
        middleCard: ["middleCard_", 0x30, "ints"],
        tailCard: ["tailCard_", 0x38, "ints"],
        abandonCard: ["abandonCard_", 0x40, "ints"],
        headType: ["headType_", 0x48, "i32"],
        middleType: ["middleType_", 0x4C, "i32"],
        tailType: ["tailType_", 0x50, "i32"],
        bust: ["bust_", 0x54, "bool"],
        handCard: ["handCard_", 0x58, "ints"],
        round: ["round_", 0x60, "i32"],
    },
    PinePlayingStatus: {
        uid: ["uid_", 0x1C, "i32"],
        seatId: ["seatid_", 0x20, "i32"],
        card: ["card_", 0x28, "obj:PineCard"],
        fantasy: ["fantasy_", 0x38, "i32"],
        sittingOut: ["sittingOut_", 0x3C, "bool"],
        actionLeftTime: ["actionLeftTime_", 0x40, "i32"],
        name: ["name_", 0x50, "str"],
        chips: ["chips_", 0x58, "i64"],
        ready: ["ready_", 0x60, "bool"],
    },
    PinePlayerResult: {
        uid: ["uid_", 0x1C, "i32"],
        seatId: ["seatid_", 0x20, "i32"],
        card: ["card_", 0x28, "obj:PineCard"],
        fantasy: ["fantasy_", 0x30, "i32"],
        name: ["name_", 0x38, "str"],
        chips: ["chips_", 0x40, "i64"],
        scores: ["scores_", 0x50, "list:PineResultScore"],
    },
    PineResultScore: {
        uid: ["uid_", 0x1C, "i32"],
        seatId: ["seatid_", 0x20, "i32"],
        headScore: ["headScore_", 0x24, "i32"],
        middleScore: ["middleScore_", 0x28, "i32"],
        tailScore: ["tailScore_", 0x2C, "i32"],
        allwinScore: ["allwinScore_", 0x30, "i32"],
        profit: ["profit_", 0x38, "i64"],
    },
    PineGameStartInfo: {
        seatId: ["seatid_", 0x1C, "i32"],
        chips: ["chips_", 0x20, "i64"],
    },
};

// RepeatedField<T> lays its backing array at +0x10 and its count at +0x18;
// the array's elements start at +0x20. Same on both 64-bit targets.
var LIST_ARRAY = 0x10;
var LIST_COUNT = 0x18;
var ARRAY_FIRST = 0x20;
// A count past this is a sign the pointer is not what we think it is, and
// reading on it would walk into unrelated memory.
var SANE_COUNT = 400;

var il2cpp = {};
var resolved = { fields: {}, byName: 0, byFallback: 0, missing: [] };

// ------------------------------------------------------------------ runtime

function bindRuntime() {
    var module = null;
    for (var i = 0; i < MODULE_NAMES.length; i++) {
        try {
            module = Process.getModuleByName(MODULE_NAMES[i]);
            break;
        } catch (e) { /* try the next name */ }
    }
    if (module === null) {
        throw new Error("no IL2CPP module: looked for " + MODULE_NAMES.join(", "));
    }

    function fn(name, ret, args) {
        var address = module.findExportByName(name);
        if (address === null) {
            throw new Error("the IL2CPP runtime does not export " + name +
                            " -- this build may be stripped");
        }
        return new NativeFunction(address, ret, args);
    }

    il2cpp.module = module;
    il2cpp.domain_get = fn("il2cpp_domain_get", "pointer", []);
    il2cpp.domain_get_assemblies = fn("il2cpp_domain_get_assemblies", "pointer",
                                      ["pointer", "pointer"]);
    il2cpp.assembly_get_image = fn("il2cpp_assembly_get_image", "pointer", ["pointer"]);
    il2cpp.image_get_class_count = fn("il2cpp_image_get_class_count", "uint32", ["pointer"]);
    il2cpp.image_get_class = fn("il2cpp_image_get_class", "pointer", ["pointer", "uint32"]);
    il2cpp.class_get_name = fn("il2cpp_class_get_name", "pointer", ["pointer"]);
    il2cpp.class_get_method_from_name = fn("il2cpp_class_get_method_from_name",
                                           "pointer", ["pointer", "pointer", "int"]);
    il2cpp.class_get_field_from_name = fn("il2cpp_class_get_field_from_name",
                                          "pointer", ["pointer", "pointer"]);
    il2cpp.field_get_offset = fn("il2cpp_field_get_offset", "uint32", ["pointer"]);
    il2cpp.object_get_class = fn("il2cpp_object_get_class", "pointer", ["pointer"]);
    il2cpp.string_chars = fn("il2cpp_string_chars", "pointer", ["pointer"]);
    il2cpp.string_length = fn("il2cpp_string_length", "int", ["pointer"]);
    return module;
}

function className(klass) {
    if (klass === null || klass.isNull()) return "";
    var namePtr = il2cpp.class_get_name(klass);
    return namePtr.isNull() ? "" : namePtr.readUtf8String();
}

/* Walk every loaded assembly for a class of this name.
 *
 * By name rather than by (namespace, name) because the namespace is exactly
 * the sort of thing that changes between client versions without anything
 * else changing, and it buys nothing here: there is one Network class. */
function findClass(wanted) {
    var countPtr = Memory.alloc(Process.pointerSize);
    var assemblies = il2cpp.domain_get_assemblies(il2cpp.domain_get(), countPtr);
    var assemblyCount = countPtr.readULong();

    for (var a = 0; a < assemblyCount; a++) {
        var assembly = assemblies.add(a * Process.pointerSize).readPointer();
        if (assembly.isNull()) continue;
        var image = il2cpp.assembly_get_image(assembly);
        if (image.isNull()) continue;

        var classes = il2cpp.image_get_class_count(image);
        for (var c = 0; c < classes; c++) {
            var klass = il2cpp.image_get_class(image, c);
            if (klass.isNull()) continue;
            if (className(klass) === wanted) return klass;
        }
    }
    return null;
}

/* The address of a method's compiled code.
 *
 * MethodInfo begins with its own function pointer, and has since IL2CPP's
 * first release -- it is what the runtime calls through. */
function methodPointer(klass, name, argc) {
    var method = il2cpp.class_get_method_from_name(
        klass, Memory.allocUtf8String(name), argc);
    if (method.isNull()) return null;
    var pointer = method.readPointer();
    return pointer.isNull() ? null : pointer;
}

// ------------------------------------------------------------------- fields

/* Where a field sits, asked of the runtime and remembered.
 *
 * Cached per class pointer rather than per type name: two builds cannot be
 * loaded at once, and the pointer is what the lookup actually costs. */
function fieldOffset(klass, typeName, key, csharpName, fallback) {
    var cacheKey = klass.toString() + ":" + csharpName;
    if (cacheKey in resolved.fields) return resolved.fields[cacheKey];

    var offset = null;
    try {
        var field = il2cpp.class_get_field_from_name(
            klass, Memory.allocUtf8String(csharpName));
        if (!field.isNull()) offset = il2cpp.field_get_offset(field);
    } catch (e) { /* fall through to the dumped offset */ }

    if (offset === null || offset === 0) {
        offset = fallback;
        resolved.byFallback += 1;
        resolved.missing.push(typeName + "." + csharpName);
    } else {
        resolved.byName += 1;
    }
    resolved.fields[cacheKey] = offset;
    return offset;
}

// -------------------------------------------------------------- primitives

function readString(pointer) {
    if (pointer === null || pointer.isNull()) return "";
    try {
        var length = il2cpp.string_length(pointer);
        if (length <= 0 || length > 1000) return "";
        return il2cpp.string_chars(pointer).readUtf16String(length);
    } catch (e) {
        return "";
    }
}

function readIntList(pointer) {
    if (pointer === null || pointer.isNull()) return [];
    try {
        var array = pointer.add(LIST_ARRAY).readPointer();
        var count = pointer.add(LIST_COUNT).readS32();
        if (array.isNull() || count <= 0 || count > SANE_COUNT) return [];
        var out = [];
        for (var i = 0; i < count; i++) {
            out.push(array.add(ARRAY_FIRST + i * 4).readS32());
        }
        return out;
    } catch (e) {
        return [];
    }
}

function readObjectList(pointer, typeName) {
    if (pointer === null || pointer.isNull()) return [];
    try {
        var array = pointer.add(LIST_ARRAY).readPointer();
        var count = pointer.add(LIST_COUNT).readS32();
        if (array.isNull() || count <= 0 || count > SANE_COUNT) return [];
        var out = [];
        for (var i = 0; i < count; i++) {
            var element = array.add(ARRAY_FIRST + i * Process.pointerSize).readPointer();
            if (element.isNull()) continue;
            var value = readObject(element, typeName);
            if (value !== null) out.push(value);
        }
        return out;
    } catch (e) {
        return [];
    }
}

/* One managed object, by the schema for its type.
 *
 * The class comes from the object itself, not from a lookup by name: a
 * nested object already carries the runtime type it was allocated as, and
 * trusting that is both cheaper and correct where a name would be ambiguous. */
function readObject(pointer, typeName) {
    if (pointer === null || pointer.isNull()) return null;
    var schema = SCHEMA[typeName];
    if (schema === undefined) return null;

    var klass;
    try {
        klass = il2cpp.object_get_class(pointer);
    } catch (e) {
        return { _error: "no class for " + typeName };
    }
    if (klass.isNull()) return { _error: "no class for " + typeName };

    var out = {};
    for (var key in schema) {
        if (!Object.prototype.hasOwnProperty.call(schema, key)) continue;
        var spec = schema[key];
        var offset = fieldOffset(klass, typeName, key, spec[0], spec[1]);
        try {
            out[key] = readField(pointer.add(offset), spec[2]);
        } catch (e) {
            out[key] = null;
        }
    }
    return out;
}

function readField(at, kind) {
    switch (kind) {
        case "i32":  return at.readS32();
        case "i64":  return at.readS64().toNumber();
        case "bool": return at.readU8() !== 0;
        case "str":  return readString(at.readPointer());
        case "ints": return readIntList(at.readPointer());
        default:
            if (kind.indexOf("obj:") === 0) {
                return readObject(at.readPointer(), kind.substring(4));
            }
            if (kind.indexOf("list:") === 0) {
                return readObjectList(at.readPointer(), kind.substring(5));
            }
            return null;
    }
}

// ------------------------------------------------------------------ hooking

function install() {
    var module = bindRuntime();
    send({ type: "status", message: "IL2CPP runtime: " + module.name +
                                    " @ " + module.base });

    var klass = findClass(TARGET_CLASS);
    if (klass === null) {
        throw new Error("class " + TARGET_CLASS + " was not found in any " +
                        "loaded assembly");
    }
    var target = methodPointer(klass, TARGET_METHOD, TARGET_ARGC);
    if (target === null) {
        throw new Error(TARGET_CLASS + "." + TARGET_METHOD + " taking " +
                        TARGET_ARGC + " arguments was not found");
    }
    send({ type: "status",
           message: "resolved " + TARGET_CLASS + "." + TARGET_METHOD +
                    " at " + target + " (" +
                    target.sub(module.base) + " into the module)" });

    Interceptor.attach(target, {
        onEnter: function (args) {
            // IL2CPP instance call: this, then the declared arguments.
            var packet = args[1];
            var tableId = args[2].toInt32();
            if (packet.isNull()) return;

            var name;
            try {
                name = className(il2cpp.object_get_class(packet));
            } catch (e) {
                return;
            }
            if (OFC_PACKETS.indexOf(name) < 0) return;

            var data;
            try {
                data = readObject(packet, name);
            } catch (e) {
                data = { _error: e.message };
            }
            send({ type: "packet", name: name, tableId: tableId, data: data });
        }
    });

    // Said once, after the first packets have had a chance to resolve their
    // fields, so the numbers mean something.
    setTimeout(function () {
        send({ type: "status",
               message: "field offsets: " + resolved.byName + " from metadata, " +
                        resolved.byFallback + " from the dumped fallback" +
                        (resolved.missing.length
                            ? " (" + resolved.missing.join(", ") + ")"
                            : "") });
    }, 5000);

    send({ type: "status", message: "OFC reader installed, following the table" });
}

try {
    install();
} catch (error) {
    send({ type: "error", message: String(error.message || error) });
}
