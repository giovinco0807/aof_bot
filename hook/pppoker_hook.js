/**
 * Frida hook script for PPPoker - intercepts protobuf packets.
 *
 * Hooks Network.OnDispatchPacket(IMessage packet, int tableId) in GameAssembly.dll.
 * For each packet, sends the class name + field data to Python via Frida messaging.
 *
 * IL2CPP x64 calling convention:
 *   void OnDispatchPacket(Network* this, Il2CppObject* packet, int tableId, MethodInfo* method)
 *   RCX = this, RDX = packet, R8D = tableId, R9 = method
 *
 * Field offsets verified from Il2CppDumper dump.cs output.
 */

"use strict";

// RVA offsets from Il2CppDumper (added to GameAssembly.dll base)
const RVA_OnDispatchPacket = 0x28F2FA0;
const RVA_GetPacketKey = 0x28F2760;
const RVA_SendPacket = 0x28F3E60;

// IL2CPP exported functions
let il2cpp_object_get_class = null;
let il2cpp_class_get_name = null;
let il2cpp_class_get_namespace = null;
let il2cpp_string_chars = null;
let il2cpp_string_length = null;

// Packet types we care about for AoF
const INTERESTING_PACKETS = new Set([
    "EnterRoomRSP",
    "RoundStartBRC",
    "HandCardRSP",
    "ActionBRC",
    "ActionNotifyBRC",
    "ShowHandRSP",
    "WinnerRSP",
    "RoundOverBRC",
    "SitDownBRC",
    "StandUpBRC",
    "OtherEnterRoomBRC",
    // Club
    "ClubRoomRSP",
    // OFC (Pineapple) packets
    "PineHandCardBRC",
    "PineActionBRC",
    "PineResultBRC",
    "PineGameStartBRC",
    "PineSitDownBRC",
    "PineStandUpBRC",
    "PineRoomStatusBRC",
    // CAPTCHA
    "ShowCaptchaRSP",
    "CaptchaRSP",
]);

function initIl2cppExports(gameAssembly) {
    il2cpp_object_get_class = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_object_get_class"),
        "pointer", ["pointer"]
    );
    il2cpp_class_get_name = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_class_get_name"),
        "pointer", ["pointer"]
    );
    il2cpp_class_get_namespace = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_class_get_namespace"),
        "pointer", ["pointer"]
    );
    il2cpp_string_chars = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_string_chars"),
        "pointer", ["pointer"]
    );
    il2cpp_string_length = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_string_length"),
        "int", ["pointer"]
    );
}

function getClassName(obj) {
    if (obj.isNull()) return null;
    const klass = il2cpp_object_get_class(obj);
    if (klass.isNull()) return null;
    const namePtr = il2cpp_class_get_name(klass);
    if (namePtr.isNull()) return null;
    return namePtr.readUtf8String();
}

function readIl2cppString(strObj) {
    if (strObj.isNull()) return "";
    try {
        const len = il2cpp_string_length(strObj);
        if (len <= 0 || len > 1000) return "";
        const chars = il2cpp_string_chars(strObj);
        return chars.readUtf16String(len);
    } catch (e) {
        return "";
    }
}

// ============ Protobuf Field Readers (offsets from Il2CppDumper) ============

// Dump raw hex bytes from an object for reverse-engineering unknown packets
function dumpObjectHex(ptr, length) {
    try {
        return ArrayBuffer.wrap(ptr, length)
            ? ptr.readByteArray(length)
                ? Array.from(new Uint8Array(ptr.readByteArray(length)))
                    .map(b => ('0' + b.toString(16)).slice(-2))
                    .join(' ')
                : ""
            : "";
    } catch (e) {
        try {
            const bytes = [];
            for (let i = 0; i < length; i++) {
                bytes.push(('0' + ptr.add(i).readU8().toString(16)).slice(-2));
            }
            return bytes.join(' ');
        } catch (e2) {
            return "dump_error: " + e2.message;
        }
    }
}

function readPacketFields(obj, className) {
    const data = {};
    try {
        switch (className) {
            case "ActionBRC":
                // seatid_=0x1C, actionType_=0x20, chips_=0x28, handChips_=0x30
                data.seatId = obj.add(0x1C).readS32();
                data.actionType = obj.add(0x20).readS32();
                data.chips = obj.add(0x28).readS64().toNumber();
                data.handChips = obj.add(0x30).readS64().toNumber();
                break;

            case "RoundStartBRC":
                // stage_=0x1C, board_=0x20(RepeatedField<int>), curBlind_=0x28, handCard_=0x40
                data.stage = obj.add(0x1C).readS32();
                data.curBlind = obj.add(0x28).readS64().toNumber();
                data.board = readRepeatedInt(obj.add(0x20).readPointer());
                const handCardPtr = obj.add(0x40).readPointer();
                if (!handCardPtr.isNull()) {
                    data.handCard = readHandCard(handCardPtr);
                }
                break;

            case "HandCardRSP":
                // HandCardRSP has same layout as HandCard embedded object
                // card1_=0x1C, card2_=0x20, card3_=0x24, card4_=0x28,
                // card5_=0x2C, defaultCard_=0x30, card6_=0x34,
                // canViewHand_=0x38, isAllin_=0x39
                data.card1 = obj.add(0x1C).readS32();
                data.card2 = obj.add(0x20).readS32();
                data.card3 = obj.add(0x24).readS32();
                data.card4 = obj.add(0x28).readS32();
                data.card5 = obj.add(0x2C).readS32();
                data.card6 = obj.add(0x34).readS32();
                data.canViewHand = obj.add(0x38).readU8() !== 0;
                data.isAllin = obj.add(0x39).readU8() !== 0;
                break;

            case "ActionNotifyBRC":
                // seatid_=0x1C, timeout_=0x20
                data.seatId = obj.add(0x1C).readS32();
                data.timeout = obj.add(0x20).readS32();
                break;

            case "ShowHandRSP":
                // info_=0x20(RepeatedField<ShowHandInfo>), winnerSeatid_=0x28(RepeatedField<int>)
                data.info = readRepeatedShowHandInfo(obj.add(0x20).readPointer());
                data.winnerSeatIds = readRepeatedInt(obj.add(0x28).readPointer());
                break;

            case "WinnerRSP":
                // winner_=0x20(RepeatedField<WinningInfo>), profit_=0x28(RepeatedField<WinningProfit>)
                data.winners = readRepeatedWinningInfo(obj.add(0x20).readPointer());
                data.profits = readRepeatedWinningProfit(obj.add(0x28).readPointer());
                break;

            case "EnterRoomRSP":
                // code_=0x1C, reason_=0x20, tableStatus_=0x28, roomStatus_=0x30,
                // playingStatus_=0x38, roomInfo_=0x40, roomType_=0x48, roomid_=0x60
                data.code = obj.add(0x1C).readS32();
                data.roomType = obj.add(0x48).readS32();
                data.roomId = obj.add(0x60).readS32();
                const tsPtr = obj.add(0x28).readPointer();
                if (!tsPtr.isNull()) {
                    data.tableStatus = readTableStatus(tsPtr);
                }
                const riPtr = obj.add(0x40).readPointer();
                if (!riPtr.isNull()) {
                    data.roomInfo = readRoomInfo(riPtr);
                }
                break;

            case "SitDownBRC":
                // seatid_=0x1C, chips_=0x20, brief_=0x28, status_=0x30
                data.seatId = obj.add(0x1C).readS32();
                data.chips = obj.add(0x20).readS64().toNumber();
                const briefPtr = obj.add(0x28).readPointer();
                if (!briefPtr.isNull()) {
                    data.player = readUserBrief(briefPtr);
                }
                break;

            case "RoundOverBRC":
                // pool_=0x18(RepeatedField<long>)
                data.pool = readRepeatedLong(obj.add(0x18).readPointer());
                break;

            case "StandUpBRC":
                // seatid_=0x1C, code_=0x20
                data.seatId = obj.add(0x1C).readS32();
                data.code = obj.add(0x20).readS32();
                break;

            case "OtherEnterRoomBRC":
                // user_=0x18(UserBrief), frameId_=0x20
                const otherUserPtr = obj.add(0x18).readPointer();
                if (!otherUserPtr.isNull()) {
                    data.user = readUserBrief(otherUserPtr);
                }
                const frameIdPtr = obj.add(0x20).readPointer();
                if (!frameIdPtr.isNull()) {
                    data.frameId = readIl2cppString(frameIdPtr);
                }
                break;

            // ============ Club Packets ============

            case "ClubRoomRSP":
                // leagueid_=0x1C, info_=0x20(RepeatedField<ClubRoomInfo>),
                // clubid_=0x28, code_=0x2C, roomNum_=0x38
                data.leagueId = obj.add(0x1C).readS32();
                data.clubId = obj.add(0x28).readS32();
                data.code = obj.add(0x2C).readS32();
                data.roomNum = obj.add(0x38).readS32();
                data.rooms = readRepeatedClubRoomInfo(obj.add(0x20).readPointer());
                break;

            // ============ OFC (Pineapple) Packets ============

            case "PineHandCardBRC":
                // handCard_=0x20(RepeatedField<PineHandCard>), actionSeatid_=0x28
                data.handCards = readRepeatedPineHandCard(obj.add(0x20).readPointer());
                data.actionSeatId = obj.add(0x28).readS32();
                break;

            case "PineActionBRC":
                // uid_=0x1C, seatid_=0x20, card_=0x28(PineCard),
                // headCard_=0x30, middleCard_=0x38, tailCard_=0x40
                data.uid = obj.add(0x1C).readS32();
                data.seatId = obj.add(0x20).readS32();
                const pabCardPtr = obj.add(0x28).readPointer();
                if (!pabCardPtr.isNull()) {
                    data.card = readPineCard(pabCardPtr);
                }
                data.headCard = readRepeatedInt(obj.add(0x30).readPointer());
                data.middleCard = readRepeatedInt(obj.add(0x38).readPointer());
                data.tailCard = readRepeatedInt(obj.add(0x40).readPointer());
                break;

            case "PineResultBRC":
                // playerResult_=0x18(RepeatedField<PinePlayerResult>)
                data.playerResults = readRepeatedPinePlayerResult(obj.add(0x18).readPointer());
                break;

            case "PineGameStartBRC":
                // dealerSeatid_=0x1C, leftTime_=0x20,
                // startInfo_=0x28(RepeatedField<PineGameStartInfo>),
                // actionType_=0x30, gameid_=0x38
                data.dealerSeatId = obj.add(0x1C).readS32();
                data.leftTime = obj.add(0x20).readS32();
                data.actionType = obj.add(0x30).readS32();
                const gameIdPtr = obj.add(0x38).readPointer();
                if (!gameIdPtr.isNull()) {
                    data.gameId = readIl2cppString(gameIdPtr);
                }
                data.startInfo = readRepeatedPineStartInfo(obj.add(0x28).readPointer());
                break;

            case "PineSitDownBRC":
                // pinePlayingStatus_=0x18(PinePlayingStatus)
                const psdPtr = obj.add(0x18).readPointer();
                if (!psdPtr.isNull()) {
                    data.player = readPinePlayingStatus(psdPtr);
                }
                break;

            case "PineStandUpBRC":
                // seatid_=0x1C, code_=0x20
                data.seatId = obj.add(0x1C).readS32();
                data.code = obj.add(0x20).readS32();
                break;

            case "PineRoomStatusBRC":
                // pinePlayingStatus_=0x18(RepeatedField<PinePlayingStatus>)
                data.players = readRepeatedPinePlayingStatus(obj.add(0x18).readPointer());
                break;

            case "ShowCaptchaRSP":
                // Fields decoded from hex dump analysis:
                // 0x18: operand1 (int32) - first number in math question
                // 0x1C: operand2 (int32) - second number (or captcha type)
                // 0x20: operator (int32) - 0=add, 1=sub, 2=mul?
                // 0x28: answer choices (pointer to RepeatedField<int> or similar)
                // 0x30: pointer (possibly more choices or image data)
                // 0x38: timeout (int32, seconds)
                data.operand1 = obj.add(0x18).readS32();
                data.operand2 = obj.add(0x1C).readS32();
                data.operator = obj.add(0x20).readS32();
                data.timeout = obj.add(0x38).readS32();
                // Try reading answer choices from RepeatedField at 0x28
                try {
                    data.choices = readRepeatedInt(obj.add(0x28).readPointer());
                } catch(e) { data.choices = []; }
                // Also try at 0x30
                try {
                    data.choices2 = readRepeatedInt(obj.add(0x30).readPointer());
                } catch(e) { data.choices2 = []; }
                // Raw hex for debugging (first 128 bytes)
                data._rawHex = dumpObjectHex(obj, 128);
                break;

            case "CaptchaRSP":
                // Response/result of CAPTCHA
                data.code = obj.add(0x18).readS32();
                data.result = obj.add(0x1C).readS32();
                data._rawHex = dumpObjectHex(obj, 128);
                break;

            default:
                break;
        }
    } catch (e) {
        data._error = e.message;
    }
    return data;
}

// HandCardRSP: card1_=0x1C, card2_=0x20, card3_=0x24, card4_=0x28,
//              card5_=0x2C, defaultCard_=0x30, card6_=0x34,
//              canViewHand_=0x38, isAllin_=0x39
function readHandCard(ptr) {
    if (ptr.isNull()) return null;
    try {
        return {
            card1: ptr.add(0x1C).readS32(),
            card2: ptr.add(0x20).readS32(),
            card3: ptr.add(0x24).readS32(),
            card4: ptr.add(0x28).readS32(),
            card5: ptr.add(0x2C).readS32(),
            card6: ptr.add(0x34).readS32(),
            canViewHand: ptr.add(0x38).readU8() !== 0,
            isAllin: ptr.add(0x39).readU8() !== 0,
        };
    } catch (e) {
        return { _error: e.message };
    }
}

// UserBrief: uid_=0x20, name_=0x28, iconUrl_=0x30, clubid_=0x40
function readUserBrief(ptr) {
    if (ptr.isNull()) return null;
    try {
        const uid = ptr.add(0x20).readS64().toNumber();
        const namePtr = ptr.add(0x28).readPointer();
        let name = "";
        if (!namePtr.isNull()) {
            name = readIl2cppString(namePtr);
        }
        const clubId = ptr.add(0x40).readS32();
        return { uid, name, clubId };
    } catch (e) {
        return { _error: e.message };
    }
}

// TableStatus: isPlaying_=0x1C, actionIdx_=0x20, dIdx_=0x24, sbIdx_=0x28,
//              bbIdx_=0x2C, seat_=0x30(RepeatedField<SeatStatus>),
//              pool_=0x38(RepeatedField<long>), stage_=0x40,
//              board_=0x48(RepeatedField<int>), tid_=0x50,
//              gameType_=0x84, curBlind_=0x88
function readTableStatus(ptr) {
    if (ptr.isNull()) return null;
    try {
        return {
            isPlaying: ptr.add(0x1C).readU8() !== 0,
            actionIdx: ptr.add(0x20).readS32(),
            dealerIdx: ptr.add(0x24).readS32(),
            sbIdx: ptr.add(0x28).readS32(),
            bbIdx: ptr.add(0x2C).readS32(),
            stage: ptr.add(0x40).readS32(),
            board: readRepeatedInt(ptr.add(0x48).readPointer()),
            tid: ptr.add(0x50).readS32(),
            gameType: ptr.add(0x84).readS32(),
            curBlind: ptr.add(0x88).readS64().toNumber(),
            seats: readRepeatedSeatStatus(ptr.add(0x30).readPointer()),
        };
    } catch (e) {
        return { _error: e.message };
    }
}

// SeatStatus: seatid_=0x20, actionType_=0x24, player_=0x28(UserBrief),
//             handChips_=0x30, destopChips_=0x38, hasCard_=0x40,
//             status_=0x78, card1_=0x98..card5_=0xA8, card6_=0xBC,
//             isInGame_=0xD8
function readSeatStatus(ptr) {
    if (ptr.isNull()) return null;
    try {
        const data = {
            seatId: ptr.add(0x20).readS32(),
            actionType: ptr.add(0x24).readS32(),
            handChips: ptr.add(0x30).readS64().toNumber(),
            desktopChips: ptr.add(0x38).readS64().toNumber(),
            hasCard: ptr.add(0x40).readU8() !== 0,
            status: ptr.add(0x78).readS32(),
            isInGame: ptr.add(0xD8).readU8() !== 0,
            card1: ptr.add(0x98).readS32(),
            card2: ptr.add(0x9C).readS32(),
            card3: ptr.add(0xA0).readS32(),
            card4: ptr.add(0xA4).readS32(),
            card5: ptr.add(0xA8).readS32(),
        };
        const playerPtr = ptr.add(0x28).readPointer();
        if (!playerPtr.isNull()) {
            data.player = readUserBrief(playerPtr);
        }
        return data;
    } catch (e) {
        return { _error: e.message };
    }
}

// RoomInfo: roomid_=0x24, blind_=0x38, ante_=0x40, minBuyin_=0x48,
//           actionTime_=0x50, seatNum_=0x58, roomType_=0x68,
//           feetype_=0x6C, feepoint_=0x70, cap_=0xA0,
//           maxBuyin_=0xA8, gameMode_=0xF0
function readRoomInfo(ptr) {
    if (ptr.isNull()) return null;
    try {
        return {
            roomId: ptr.add(0x24).readS32(),
            blind: ptr.add(0x38).readS64().toNumber(),
            ante: ptr.add(0x40).readS64().toNumber(),
            minBuyin: ptr.add(0x48).readS64().toNumber(),
            actionTime: ptr.add(0x50).readS32(),
            seatNum: ptr.add(0x58).readS32(),
            roomType: ptr.add(0x68).readS32(),
            feeType: ptr.add(0x6C).readS32(),
            feePoint: ptr.add(0x70).readS32(),
            cap: ptr.add(0xA0).readS32(),
            maxBuyin: ptr.add(0xA8).readS64().toNumber(),
            gameMode: ptr.add(0xF0).readS32(),
        };
    } catch (e) {
        return { _error: e.message };
    }
}

// ShowHandInfo: seatid_=0x1C, card1_=0x20..card5_=0x30, card6_=0x34
function readShowHandInfo(ptr) {
    if (ptr.isNull()) return null;
    try {
        return {
            seatId: ptr.add(0x1C).readS32(),
            card1: ptr.add(0x20).readS32(),
            card2: ptr.add(0x24).readS32(),
            card3: ptr.add(0x28).readS32(),
            card4: ptr.add(0x2C).readS32(),
            card5: ptr.add(0x30).readS32(),
            card6: ptr.add(0x34).readS32(),
        };
    } catch (e) {
        return { _error: e.message };
    }
}

// WinningInfo: seatid_=0x1C, poolid_=0x20, chips_=0x28, type_=0x30, uid_=0x38
function readWinningInfo(ptr) {
    if (ptr.isNull()) return null;
    try {
        return {
            seatId: ptr.add(0x1C).readS32(),
            poolId: ptr.add(0x20).readS32(),
            chips: ptr.add(0x28).readS64().toNumber(),
            handType: ptr.add(0x30).readS32(),
            uid: ptr.add(0x38).readS64().toNumber(),
        };
    } catch (e) {
        return { _error: e.message };
    }
}

// WinningProfit: seatid_=0x1C, chips_=0x20
function readWinningProfit(ptr) {
    if (ptr.isNull()) return null;
    try {
        return {
            seatId: ptr.add(0x1C).readS32(),
            chips: ptr.add(0x20).readS64().toNumber(),
        };
    } catch (e) {
        return { _error: e.message };
    }
}

// ============ Club Field Readers ============

// ClubRoomInfo: roomid_=0x24, roomName_=0x28, ownerName_=0x30, blind_=0x40,
//               seatNum_=0x58, players_=0x5C, isStarted_=0x60, roomType_=0x68,
//               buyin_=0x70, currentPlayerNum_=0x7C
function readClubRoomInfo(ptr) {
    if (ptr === null || ptr.isNull()) return null;
    try {
        const data = {
            roomId: ptr.add(0x24).readS32(),
            blind: ptr.add(0x40).readS64().toNumber(),
            seatNum: ptr.add(0x58).readS32(),
            players: ptr.add(0x5C).readS32(),
            isStarted: ptr.add(0x60).readU8() !== 0,
            roomType: ptr.add(0x68).readS32(),
            buyin: ptr.add(0x70).readS64().toNumber(),
            currentPlayerNum: ptr.add(0x7C).readS32(),
        };
        const namePtr = ptr.add(0x28).readPointer();
        if (!namePtr.isNull()) {
            data.roomName = readIl2cppString(namePtr);
        }
        const ownerPtr = ptr.add(0x30).readPointer();
        if (!ownerPtr.isNull()) {
            data.ownerName = readIl2cppString(ownerPtr);
        }
        return data;
    } catch (e) {
        return { _error: e.message };
    }
}

function readRepeatedClubRoomInfo(ptr) {
    if (ptr === null || ptr.isNull()) return [];
    try {
        const arrayPtr = ptr.add(0x10).readPointer();
        const count = ptr.add(0x18).readS32();
        if (arrayPtr.isNull() || count <= 0 || count > 100) return [];
        const result = [];
        for (let i = 0; i < count; i++) {
            const p = arrayPtr.add(0x20 + i * 8).readPointer();
            if (!p.isNull()) result.push(readClubRoomInfo(p));
        }
        return result;
    } catch (e) { return []; }
}

// ============ OFC (Pineapple) Field Readers ============

// PineHandCard: card_=0x20(RepeatedField<int>), round_=0x28, fantasy_=0x2C,
//               uid_=0x30, seatid_=0x34, actionLeftTime_=0x3C
function readPineHandCard(ptr) {
    if (ptr === null || ptr.isNull()) return null;
    try {
        return {
            cards: readRepeatedInt(ptr.add(0x20).readPointer()),
            round: ptr.add(0x28).readS32(),
            fantasy: ptr.add(0x2C).readS32(),
            uid: ptr.add(0x30).readS32(),
            seatId: ptr.add(0x34).readS32(),
            actionLeftTime: ptr.add(0x3C).readS32(),
        };
    } catch (e) {
        return { _error: e.message };
    }
}

// PineCard: headScore_=0x1C, middleScore_=0x20, tailScore_=0x24,
//           headCard_=0x28, middleCard_=0x30, tailCard_=0x38,
//           abandonCard_=0x40, headType_=0x48, middleType_=0x4C,
//           tailType_=0x50, bust_=0x54, handCard_=0x58, round_=0x60
function readPineCard(ptr) {
    if (ptr === null || ptr.isNull()) return null;
    try {
        return {
            headScore: ptr.add(0x1C).readS32(),
            middleScore: ptr.add(0x20).readS32(),
            tailScore: ptr.add(0x24).readS32(),
            headCard: readRepeatedInt(ptr.add(0x28).readPointer()),
            middleCard: readRepeatedInt(ptr.add(0x30).readPointer()),
            tailCard: readRepeatedInt(ptr.add(0x38).readPointer()),
            abandonCard: readRepeatedInt(ptr.add(0x40).readPointer()),
            headType: ptr.add(0x48).readS32(),
            middleType: ptr.add(0x4C).readS32(),
            tailType: ptr.add(0x50).readS32(),
            bust: ptr.add(0x54).readU8() !== 0,
            handCard: readRepeatedInt(ptr.add(0x58).readPointer()),
            round: ptr.add(0x60).readS32(),
        };
    } catch (e) {
        return { _error: e.message };
    }
}

// PinePlayingStatus: uid_=0x1C, seatid_=0x20, card_=0x28(PineCard),
//                    fantasy_=0x38, sittingOut_=0x3C, actionLeftTime_=0x40,
//                    name_=0x50, chips_=0x58, ready_=0x60
function readPinePlayingStatus(ptr) {
    if (ptr === null || ptr.isNull()) return null;
    try {
        const data = {
            uid: ptr.add(0x1C).readS32(),
            seatId: ptr.add(0x20).readS32(),
            fantasy: ptr.add(0x38).readS32(),
            sittingOut: ptr.add(0x3C).readU8() !== 0,
            actionLeftTime: ptr.add(0x40).readS32(),
            chips: ptr.add(0x58).readS64().toNumber(),
            ready: ptr.add(0x60).readU8() !== 0,
        };
        const namePtr = ptr.add(0x50).readPointer();
        if (!namePtr.isNull()) {
            data.name = readIl2cppString(namePtr);
        }
        const cardPtr = ptr.add(0x28).readPointer();
        if (!cardPtr.isNull()) {
            data.card = readPineCard(cardPtr);
        }
        return data;
    } catch (e) {
        return { _error: e.message };
    }
}

// PineResultScore: uid_=0x1C, seatid_=0x20, headScore_=0x24,
//                  middleScore_=0x28, tailScore_=0x2C, allwinScore_=0x30, profit_=0x38
function readPineResultScore(ptr) {
    if (ptr === null || ptr.isNull()) return null;
    try {
        return {
            uid: ptr.add(0x1C).readS32(),
            seatId: ptr.add(0x20).readS32(),
            headScore: ptr.add(0x24).readS32(),
            middleScore: ptr.add(0x28).readS32(),
            tailScore: ptr.add(0x2C).readS32(),
            allwinScore: ptr.add(0x30).readS32(),
            profit: ptr.add(0x38).readS64().toNumber(),
        };
    } catch (e) {
        return { _error: e.message };
    }
}

// PinePlayerResult: uid_=0x1C, seatid_=0x20, card_=0x28(PineCard),
//                   fantasy_=0x30, name_=0x38, chips_=0x40, score_=0x50
function readPinePlayerResult(ptr) {
    if (ptr === null || ptr.isNull()) return null;
    try {
        const data = {
            uid: ptr.add(0x1C).readS32(),
            seatId: ptr.add(0x20).readS32(),
            fantasy: ptr.add(0x30).readS32(),
            chips: ptr.add(0x40).readS64().toNumber(),
        };
        const namePtr = ptr.add(0x38).readPointer();
        if (!namePtr.isNull()) {
            data.name = readIl2cppString(namePtr);
        }
        const cardPtr = ptr.add(0x28).readPointer();
        if (!cardPtr.isNull()) {
            data.card = readPineCard(cardPtr);
        }
        data.scores = readRepeatedPineResultScore(ptr.add(0x50).readPointer());
        return data;
    } catch (e) {
        return { _error: e.message };
    }
}

// ============ RepeatedField Readers ============
// Google.Protobuf.Collections.RepeatedField<T> layout:
//   0x10 = array_ (IL2CPP array pointer)
//   0x18 = count_ (int)
// IL2CPP array layout:
//   0x18 = length (bounds check)
//   0x20 = first element

function readRepeatedInt(ptr) {
    if (ptr === null || ptr.isNull()) return [];
    try {
        const arrayPtr = ptr.add(0x10).readPointer();
        const count = ptr.add(0x18).readS32();
        if (arrayPtr.isNull() || count <= 0 || count > 100) return [];
        const result = [];
        for (let i = 0; i < count; i++) {
            result.push(arrayPtr.add(0x20 + i * 4).readS32());
        }
        return result;
    } catch (e) {
        return [];
    }
}

function readRepeatedLong(ptr) {
    if (ptr === null || ptr.isNull()) return [];
    try {
        const arrayPtr = ptr.add(0x10).readPointer();
        const count = ptr.add(0x18).readS32();
        if (arrayPtr.isNull() || count <= 0 || count > 100) return [];
        const result = [];
        for (let i = 0; i < count; i++) {
            result.push(arrayPtr.add(0x20 + i * 8).readS64().toNumber());
        }
        return result;
    } catch (e) {
        return [];
    }
}

function readRepeatedShowHandInfo(ptr) {
    if (ptr === null || ptr.isNull()) return [];
    try {
        const arrayPtr = ptr.add(0x10).readPointer();
        const count = ptr.add(0x18).readS32();
        if (arrayPtr.isNull() || count <= 0 || count > 20) return [];
        const result = [];
        for (let i = 0; i < count; i++) {
            const infoPtr = arrayPtr.add(0x20 + i * 8).readPointer();
            if (!infoPtr.isNull()) {
                result.push(readShowHandInfo(infoPtr));
            }
        }
        return result;
    } catch (e) {
        return [];
    }
}

function readRepeatedWinningInfo(ptr) {
    if (ptr === null || ptr.isNull()) return [];
    try {
        const arrayPtr = ptr.add(0x10).readPointer();
        const count = ptr.add(0x18).readS32();
        if (arrayPtr.isNull() || count <= 0 || count > 20) return [];
        const result = [];
        for (let i = 0; i < count; i++) {
            const infoPtr = arrayPtr.add(0x20 + i * 8).readPointer();
            if (!infoPtr.isNull()) {
                result.push(readWinningInfo(infoPtr));
            }
        }
        return result;
    } catch (e) {
        return [];
    }
}

function readRepeatedWinningProfit(ptr) {
    if (ptr === null || ptr.isNull()) return [];
    try {
        const arrayPtr = ptr.add(0x10).readPointer();
        const count = ptr.add(0x18).readS32();
        if (arrayPtr.isNull() || count <= 0 || count > 20) return [];
        const result = [];
        for (let i = 0; i < count; i++) {
            const infoPtr = arrayPtr.add(0x20 + i * 8).readPointer();
            if (!infoPtr.isNull()) {
                result.push(readWinningProfit(infoPtr));
            }
        }
        return result;
    } catch (e) {
        return [];
    }
}

function readRepeatedSeatStatus(ptr) {
    if (ptr === null || ptr.isNull()) return [];
    try {
        const arrayPtr = ptr.add(0x10).readPointer();
        const count = ptr.add(0x18).readS32();
        if (arrayPtr.isNull() || count <= 0 || count > 10) return [];
        const result = [];
        for (let i = 0; i < count; i++) {
            const seatPtr = arrayPtr.add(0x20 + i * 8).readPointer();
            if (!seatPtr.isNull()) {
                result.push(readSeatStatus(seatPtr));
            }
        }
        return result;
    } catch (e) {
        return [];
    }
}

// ============ OFC RepeatedField Readers ============

function readRepeatedPineHandCard(ptr) {
    if (ptr === null || ptr.isNull()) return [];
    try {
        const arrayPtr = ptr.add(0x10).readPointer();
        const count = ptr.add(0x18).readS32();
        if (arrayPtr.isNull() || count <= 0 || count > 10) return [];
        const result = [];
        for (let i = 0; i < count; i++) {
            const p = arrayPtr.add(0x20 + i * 8).readPointer();
            if (!p.isNull()) result.push(readPineHandCard(p));
        }
        return result;
    } catch (e) { return []; }
}

function readRepeatedPinePlayingStatus(ptr) {
    if (ptr === null || ptr.isNull()) return [];
    try {
        const arrayPtr = ptr.add(0x10).readPointer();
        const count = ptr.add(0x18).readS32();
        if (arrayPtr.isNull() || count <= 0 || count > 10) return [];
        const result = [];
        for (let i = 0; i < count; i++) {
            const p = arrayPtr.add(0x20 + i * 8).readPointer();
            if (!p.isNull()) result.push(readPinePlayingStatus(p));
        }
        return result;
    } catch (e) { return []; }
}

function readRepeatedPinePlayerResult(ptr) {
    if (ptr === null || ptr.isNull()) return [];
    try {
        const arrayPtr = ptr.add(0x10).readPointer();
        const count = ptr.add(0x18).readS32();
        if (arrayPtr.isNull() || count <= 0 || count > 10) return [];
        const result = [];
        for (let i = 0; i < count; i++) {
            const p = arrayPtr.add(0x20 + i * 8).readPointer();
            if (!p.isNull()) result.push(readPinePlayerResult(p));
        }
        return result;
    } catch (e) { return []; }
}

function readRepeatedPineResultScore(ptr) {
    if (ptr === null || ptr.isNull()) return [];
    try {
        const arrayPtr = ptr.add(0x10).readPointer();
        const count = ptr.add(0x18).readS32();
        if (arrayPtr.isNull() || count <= 0 || count > 10) return [];
        const result = [];
        for (let i = 0; i < count; i++) {
            const p = arrayPtr.add(0x20 + i * 8).readPointer();
            if (!p.isNull()) result.push(readPineResultScore(p));
        }
        return result;
    } catch (e) { return []; }
}

function readRepeatedPineStartInfo(ptr) {
    if (ptr === null || ptr.isNull()) return [];
    try {
        const arrayPtr = ptr.add(0x10).readPointer();
        const count = ptr.add(0x18).readS32();
        if (arrayPtr.isNull() || count <= 0 || count > 10) return [];
        const result = [];
        for (let i = 0; i < count; i++) {
            const p = arrayPtr.add(0x20 + i * 8).readPointer();
            if (!p.isNull()) {
                // PineGameStartInfo: seatid_=0x1C, chips_=0x20
                result.push({
                    seatId: p.add(0x1C).readS32(),
                    chips: p.add(0x20).readS64().toNumber(),
                });
            }
        }
        return result;
    } catch (e) { return []; }
}

// ============ Anti-Detection ============

function patchFridaDetection() {
    // 1. Hide frida-agent thread name
    // Some apps enumerate threads looking for "gmain" or "frida"
    // No action needed on Windows (thread enumeration is less common)

    // 2. Intercept IsDebuggerPresent (common anti-debug check)
    try {
        const isDbg = Module.findExportByName("kernel32.dll", "IsDebuggerPresent");
        if (isDbg) {
            Interceptor.replace(isDbg, new NativeCallback(function () {
                return 0;  // Always return "no debugger"
            }, "int", []));
        }
    } catch (e) { /* ignore */ }

    // 3. Intercept CheckRemoteDebuggerPresent
    try {
        const checkRemote = Module.findExportByName("kernel32.dll", "CheckRemoteDebuggerPresent");
        if (checkRemote) {
            Interceptor.replace(checkRemote, new NativeCallback(function (hProcess, pbDebuggerPresent) {
                if (!pbDebuggerPresent.isNull()) {
                    pbDebuggerPresent.writeU8(0);  // Not being debugged
                }
                return 1;  // Success
            }, "int", ["pointer", "pointer"]));
        }
    } catch (e) { /* ignore */ }

    // 4. Intercept NtQueryInformationProcess (ProcessDebugPort / ProcessDebugFlags)
    try {
        const ntQuery = Module.findExportByName("ntdll.dll", "NtQueryInformationProcess");
        if (ntQuery) {
            const origNtQuery = new NativeFunction(ntQuery, "int",
                ["pointer", "int", "pointer", "uint", "pointer"]);
            Interceptor.replace(ntQuery, new NativeCallback(function (
                hProcess, infoClass, buffer, bufLen, retLen
            ) {
                const result = origNtQuery(hProcess, infoClass, buffer, bufLen, retLen);
                // ProcessDebugPort=7, ProcessDebugObjectHandle=30, ProcessDebugFlags=31
                if (result === 0 && !buffer.isNull()) {
                    if (infoClass === 7 || infoClass === 30) {
                        buffer.writePointer(ptr(0));  // No debug port
                    } else if (infoClass === 31) {
                        buffer.writeU32(1);  // PROCESS_DEBUG_FLAGS: no debugger
                    }
                }
                return result;
            }, "int", ["pointer", "int", "pointer", "uint", "pointer"]));
        }
    } catch (e) { /* ignore */ }

    // 5. Hide frida DLLs from module enumeration
    // Hook Module32First/Next to filter out frida-agent*.dll
    try {
        const mod32Next = Module.findExportByName("kernel32.dll", "Module32NextW");
        if (mod32Next) {
            const origNext = new NativeFunction(mod32Next, "int", ["pointer", "pointer"]);
            Interceptor.replace(mod32Next, new NativeCallback(function (hSnapshot, lpme) {
                while (true) {
                    const result = origNext(hSnapshot, lpme);
                    if (result === 0) return 0;  // No more modules
                    // MODULEENTRY32W.szModule at offset 0x20 (wchar)
                    const modName = lpme.add(0x20).readUtf16String();
                    if (modName && (modName.toLowerCase().includes("frida") ||
                        modName.toLowerCase().includes("gadget"))) {
                        continue;  // Skip frida modules
                    }
                    return result;
                }
            }, "int", ["pointer", "pointer"]));
        }
    } catch (e) { /* ignore */ }
}

// ============ Main Hook Setup ============

function main() {
    // Apply anti-detection patches first
    patchFridaDetection();

    const gameAssembly = Process.getModuleByName("GameAssembly.dll");
    if (!gameAssembly) {
        send({ type: "error", message: "GameAssembly.dll not found" });
        return;
    }

    initIl2cppExports(gameAssembly);

    // Hook OnDispatchPacket
    const onDispatchAddr = gameAssembly.base.add(RVA_OnDispatchPacket);
    let networkGcHandle = 0;  // GC handle to prevent Network instance from being collected
    let sendPacketMethodInfo = null;

    function getNetworkInstance() {
        if (networkGcHandle === 0) return null;
        return il2cpp_gchandle_get_target(networkGcHandle);
    }

    Interceptor.attach(onDispatchAddr, {
        onEnter: function (args) {
            // args[0] = this (Network*), args[1] = packet (IMessage*), args[2] = tableId
            // Capture Network instance for SendPacket calls (pin with GC handle)
            if (networkGcHandle === 0 && !args[0].isNull()) {
                networkGcHandle = il2cpp_gchandle_new(args[0], 0);  // 0 = normal handle (prevents GC)
                send({ type: "info", message: "Network instance captured: " + args[0] + " gchandle=" + networkGcHandle });
            }

            const packet = args[1];
            const tableId = args[2].toInt32();

            if (packet.isNull()) return;

            const className = getClassName(packet);
            if (!className) return;

            // Filter: only send interesting packets with full data
            if (!INTERESTING_PACKETS.has(className)) {
                // Log type only (skip HeartbeatRSP spam)
                if (className !== "HeartbeatRSP") {
                    send({ type: "packet_type", name: className, tableId: tableId });
                }
                return;
            }

            const fields = readPacketFields(packet, className);

            send({
                type: "packet",
                name: className,
                tableId: tableId,
                data: fields,
                timestamp: Date.now(),
            });
        }
    });

    // IL2CPP helpers for object creation
    const il2cpp_gchandle_new = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_gchandle_new"),
        "uint32", ["pointer", "int"]
    );
    const il2cpp_gchandle_get_target = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_gchandle_get_target"),
        "pointer", ["uint32"]
    );
    const il2cpp_object_new = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_object_new"),
        "pointer", ["pointer"]
    );
    const il2cpp_class_from_name = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_class_from_name"),
        "pointer", ["pointer", "pointer", "pointer"]
    );
    const il2cpp_domain_get = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_domain_get"),
        "pointer", []
    );
    const il2cpp_domain_get_assemblies = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_domain_get_assemblies"),
        "pointer", ["pointer", "pointer"]
    );
    const il2cpp_class_get_method_from_name = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_class_get_method_from_name"),
        "pointer", ["pointer", "pointer", "int"]
    );
    const il2cpp_assembly_get_image = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_assembly_get_image"),
        "pointer", ["pointer"]
    );

    // Find the "Pb" namespace EnterRoomREQ class
    function findClass(namespace, name) {
        const domain = il2cpp_domain_get();
        const sizePtr = Memory.alloc(4);
        const assemblies = il2cpp_domain_get_assemblies(domain, sizePtr);
        const count = sizePtr.readU32();
        const nsPtr = Memory.allocUtf8String(namespace);
        const namePtr = Memory.allocUtf8String(name);
        for (let i = 0; i < count; i++) {
            const asm = assemblies.add(i * Process.pointerSize).readPointer();
            const image = il2cpp_assembly_get_image(asm);
            const klass = il2cpp_class_from_name(image, nsPtr, namePtr);
            if (!klass.isNull()) return klass;
        }
        return null;
    }

    // RVAs for packet constructors
    const RVA_EnterRoomREQ_ctor = 0x388B990;
    const RVA_LeaveRoomREQ_ctor = 0x38678B0;

    // IL2CPP runtime invoke (safe method calling)
    const il2cpp_runtime_invoke = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_runtime_invoke"),
        "pointer", ["pointer", "pointer", "pointer", "pointer"]
    );
    const il2cpp_class_get_methods = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_class_get_methods"),
        "pointer", ["pointer", "pointer"]
    );
    const il2cpp_method_get_name = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_method_get_name"),
        "pointer", ["pointer"]
    );
    const il2cpp_method_get_param_count = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_method_get_param_count"),
        "uint32", ["pointer"]
    );

    // Get Network class and SendPacket MethodInfo from the captured this pointer
    const il2cpp_object_get_class = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_object_get_class"),
        "pointer", ["pointer"]
    );
    const il2cpp_class_get_name = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_class_get_name"),
        "pointer", ["pointer"]
    );
    const il2cpp_class_get_namespace = new NativeFunction(
        gameAssembly.getExportByName("il2cpp_class_get_namespace"),
        "pointer", ["pointer"]
    );

    function findSendPacketMethod(networkObj) {
        const klass = il2cpp_object_get_class(networkObj);
        const className = il2cpp_class_get_name(klass).readUtf8String();
        const ns = il2cpp_class_get_namespace(klass).readUtf8String();
        send({ type: "info", message: "Network object class: " + ns + "." + className });

        const iterPtr = Memory.alloc(Process.pointerSize);
        iterPtr.writePointer(ptr(0));
        let method;
        const methods = [];
        const sendMethods = [];
        while (!(method = il2cpp_class_get_methods(klass, iterPtr)).isNull()) {
            const namePtr = il2cpp_method_get_name(method);
            const name = namePtr.readUtf8String();
            const paramCount = il2cpp_method_get_param_count(method);
            methods.push(name + "(" + paramCount + ")");
            if (name.indexOf("Send") !== -1 || name.indexOf("send") !== -1) {
                sendMethods.push(name + "(" + paramCount + ")");
            }
            if (name === "SendPacket" && paramCount === 2) {
                sendPacketMethodInfo = method;
            }
        }
        send({ type: "info", message: "Network all methods (" + methods.length + "): " + methods.join(", ") });
        if (sendMethods.length > 0) {
            send({ type: "info", message: "Network Send* methods: " + sendMethods.join(", ") });
        }
        if (sendPacketMethodInfo) {
            send({ type: "info", message: "Found SendPacket MethodInfo: " + sendPacketMethodInfo });
        } else {
            send({ type: "error", message: "SendPacket(2) not found. Looking for any Send method..." });
            // Try any Send method with 2 params as fallback
            iterPtr.writePointer(ptr(0));
            while (!(method = il2cpp_class_get_methods(klass, iterPtr)).isNull()) {
                const namePtr = il2cpp_method_get_name(method);
                const name = namePtr.readUtf8String();
                const paramCount = il2cpp_method_get_param_count(method);
                if (name.indexOf("Send") !== -1 && paramCount === 2) {
                    sendPacketMethodInfo = method;
                    send({ type: "info", message: "Using fallback: " + name + "(" + paramCount + ") at " + method });
                    break;
                }
            }
        }
    }

    // Note: enter_room/leave_room via packet sending removed (SendPacket crashes).
    // Room entry is now handled via mouse click automation on the Python side.

    send({ type: "info", message: "Ready" });
}

main();
