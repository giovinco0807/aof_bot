# AoF Bot データガイド

## データベース

### `automation/data/hands.db` — メインDB（新データ）

ポジション修正済み（2026-03-14〜）。`action_order` でBBを正確に判定。

#### `hands` テーブル
| カラム | 型 | 説明 |
|---|---|---|
| id | INTEGER | ハンドID（PK） |
| num_players | INTEGER | 2/3/4 |
| dealer_seat | INTEGER | BB の seat_id（action_order[-1]） |
| player_ids | TEXT | カンマ区切り（seat順） |
| actions | TEXT | カンマ区切り（A/F/?、seat順） |
| cards | TEXT | ショーダウン時のみ記録 |
| pot_chips | REAL | ポット |
| rake_chips | REAL | レーキ |

#### `hand_players` テーブル
| カラム | 型 | 説明 |
|---|---|---|
| hand_id | INTEGER | FK → hands.id |
| player_id | TEXT | PPPoker UID |
| seat_id | INTEGER | 物理シート (0-3) |
| position | TEXT | **CO/BTN/SB/BB**（新データのみ） |
| action | TEXT | A=push, F=fold, ?=不明 |
| cards | TEXT | ショーダウン時のカード（例: `AhKd`） |
| profit_chips | REAL | 損益 |
| profit_bb | REAL | BB換算損益 |

#### `player_stats` テーブル
| カラム | 型 | 説明 |
|---|---|---|
| player_id | TEXT | PK |
| hands_seen | INTEGER | 総ハンド数 |
| hands_pushed | INTEGER | push 回数 |
| total_profit_bb | REAL | 累積損益（BB） |
| showdown_hands | TEXT | JSON配列（直近カード） |

### `automation/data/hands_old_20260314.db` — 旧データバックアップ

15,293ハンド。`dealer_seat` が固定（毎ハンド更新されていなかった）ため、3P/4P のポジションは不正確。2P はアクションパターンから推定可能。

---

## GTO チャート

### `solver/data/charts_rb50/aof_{2,3,4}p_8bb.json`

8BB、レーキ込みの GTO チャート。

```json
{
  "charts": [
    {
      "position": "CO",
      "prior_actions": "",
      "entries": [
        {"hand": "AA", "allin_freq": 1.0},
        {"hand": "AKs", "allin_freq": 1.0}
      ]
    }
  ]
}
```

---

## 分析スクリプト

### `analyze_gto_dev.py` — ポジション別 push rate vs GTO
```
python -c "from analyze_gto_dev import analyze; analyze()"
```
各プレイヤーの状況別（NP/ポジション/prior）push率を GTO と比較。

### `analyze_mistakes.py` — ハンド別ミス分析
```
python analyze_mistakes.py
```
ショーダウンカードから GTO と乖離する具体的ハンドを特定。
出力: `automation/data/player_mistakes.json`

---

## ポジション判定の仕組み

`packet_capture.py` で `ActionBRC` の到着順を `action_order` に記録。

- AoFのアクション順: **CO → BTN → SB → BB**（UTG first, BB last）
- `action_order[-1]` = BB の seat_id
- ポジション名は `POS_NAMES[num_players]` を `action_order` にマッピング

```
4P: action_order[0]=CO, [1]=BTN, [2]=SB, [3]=BB
3P: action_order[0]=BTN, [1]=SB, [2]=BB
2P: action_order[0]=SB, [1]=BB
```

## 確認コマンド

```bash
# 最新ハンドのポジション確認
python -c "import sqlite3; c=sqlite3.connect('automation/data/hands.db'); [print(r) for r in c.execute('SELECT hand_id,player_id,seat_id,position,action FROM hand_players ORDER BY id DESC LIMIT 12').fetchall()]"

# ハンド数確認
python -c "import sqlite3; c=sqlite3.connect('automation/data/hands.db'); print(c.execute('SELECT COUNT(*) FROM hands').fetchone()[0], 'hands')"
```
