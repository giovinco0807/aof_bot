# AoF Bot

PPPoker All-in or Fold + OFC (Pineapple) 用のGTOソルバー＆自動化ツール。

## 構成

```
aof_bot/
├── solver/          Rust製GTOソルバー + APIサーバー
├── hook/            Fridaパケットフック (pppoker_hook.js + packet_capture.py)
├── automation/      Python GUI + ADB自動操作
│   ├── gui.py           メインGUI (管理者権限で自動起動)
│   ├── adb_input.py     ADB経由のタップ入力 (sendevent)
│   └── data/            DB・設定ファイル
└── data/charts/     GTOチャート (JSON)
```

## セットアップ

### 必要なもの

- Python 3.10+
- Frida (`pip install frida frida-tools`)
- Rust (ソルバービルド用)
- ADB (自動操作用、scrcpy同梱のadb.exeを使用)

### インストール

```bash
pip install frida frida-tools requests
```

### ソルバービルド

```bash
cd solver
cargo build --release
```

## 使い方

### GUI起動

```bash
python automation/gui.py
```

初回起動時にUACダイアログが出る（Fridaのプロセスアタッチに管理者権限が必要）。
2回目以降も自動で管理者昇格するので、右クリック→管理者として実行は不要。

### 画面説明

#### Configuration
| 項目 | 説明 |
|------|------|
| Process | アタッチ先のプロセス名（通常 `PPPoker.exe`） |
| Hero UID | 自分のPPPoker UID（0 = 観戦モード） |
| Auto-Play (ADB) | ONにするとソルバーの判断でADB経由で自動タップ |
| Calibrate Buttons | スクリーンショットを撮ってボタン座標を設定 |
| Screenshot | ADB経由でスクリーンショット取得 |

#### Auto-Exit
| 項目 | 説明 |
|------|------|
| Max Hands | 指定ハンド数で自動退出（0 = 無制限） |
| Stop Time | 指定時刻(HH:MM)で自動退出 |
| Leave if disadvantaged | 30ハンド後に20BB以上負けていたら退出 |

#### ボタン
| ボタン | 説明 |
|--------|------|
| START | パケットキャプチャ開始（AoFソルバー連携＋自動操作） |
| STOP | キャプチャ停止 |
| MONITOR | クラブのテーブル監視モード（後述） |

#### タブ
- **Log** — リアルタイムのパケットログ、ソルバー判断、エラー等
- **Player Stats** — 対戦相手の統計（ハンド数、Push率、収支）

### モード別使い方

#### 1. 手動プレイ + ソルバーアドバイス

Hero UIDを設定し、Auto-Playは**OFF**のまま**START**を押す。
PPPokerでテーブルに入ると、ハンドごとにログに`>>> GTO: AhKd | 4p 8BB | push=72.3% -> AllIn <<<`のようなアドバイスが表示される。

#### 2. 全自動プレイ

Hero UIDを設定し、Auto-Play (ADB)を**ON**にして**START**。
Androidデバイスが接続済みであること。ソルバーの判断に基づいてFold/AllInを自動タップする。

タップの検出対策:
- `sendevent`（raw touch event）使用（`input tap`より検出困難）
- ログ正規分布の遅延（中央値~1.2秒、人間の反応時間に近似）
- 2Dガウスジッター（自然なタップ位置のばらつき）
- 15%の確率で「迷い」パターン（追加1-3秒の遅延）
- タッチ保持時間ランダム化（60-220ms）

#### 3. テーブル監視モード (MONITOR)

**MONITOR**を押すとPPPokerにアタッチし、クラブのテーブルリストを監視する。

動作:
1. クラブ画面のテーブル一覧を自動取得（`ClubRoomRSP`パケット）
2. プレイヤーが**2人以上**のテーブルを検出すると自動で入室（`EnterRoomREQ`送信）
3. 入室後はAoF/OFCのハンドデータを自動記録
4. プレイヤーが**1人以下**になったら自動退出（`LeaveRoomREQ`送信）
5. クラブ画面に戻って引き続き監視

#### 4. ADBボタンキャリブレーション

初回はFold/AllInボタンの座標を設定する必要がある:

```bash
# スクリーンショット取得
python automation/adb_input.py --screenshot

# 座標設定（対話式）
python automation/adb_input.py --calibrate

# テストタップ
python automation/adb_input.py --test fold
python automation/adb_input.py --test allin
```

設定は `automation/data/adb_config.json` に保存される。

### CLI起動（GUIなし）

```bash
# 基本（観戦モード）
python hook/packet_capture.py

# UID指定 + 自動プレイ
python hook/packet_capture.py --hero-uid 123456 --auto-play

# ハンド数制限 + 時間制限
python hook/packet_capture.py --hero-uid 123456 --auto-play --max-hands 100 --stop-time 23:30

# 不利テーブル自動退出
python hook/packet_capture.py --hero-uid 123456 --auto-play --leave-if-disadvantaged
```

### ソルバーAPIサーバー

```bash
cd solver
cargo run --release -- serve --port 8080
```

ブラウザで `http://localhost:8888` を開くとGTOチャートビューア。

APIエンドポイント:
- `GET /gto?hand=AhKd&num_players=4&stack=8` — GTO判定
- `GET /exploit?hand=AhKd&num_players=4&stack=8&player_ids=111,222` — エクスプロイト判定
- `POST /record_hand` — ハンド記録（Bayesianモデル更新）

## 対応ゲームタイプ

### AoF (All-in or Fold)
- 2/3/4人テーブル
- スタック: 1-20BB（12段階のGTOチャート）
- DCFR 100M iterationsで計算済み
- Bayesian opponent modeling + safe exploitation

### OFC (Open Face Chinese Pineapple)
- ハンドデータを自動記録（`ofc_hands`テーブル）
- 各プレイヤーのHead/Middle/Tail配置、スコア、損益を保存
- Fantasy検出
- ソルバー連携は未実装（記録のみ）

## データベース

### packets.db
全パケットの生ログ。デバッグ・分析用。

### hands.db
| テーブル | 内容 |
|----------|------|
| `hands` | AoFハンド履歴 |
| `hand_players` | ハンドごとのプレイヤー詳細 |
| `player_stats` | プレイヤー累計統計 |
| `ofc_hands` | OFCハンド履歴（player_data JSONカラム） |

## 検出対策

### Frida側 (pppoker_hook.js)
- `IsDebuggerPresent` → 常に0を返す
- `CheckRemoteDebuggerPresent` → false
- `NtQueryInformationProcess` → デバッグポート/フラグを隠蔽
- `Module32NextW` → frida/gadget DLLをモジュール一覧から除外

### ADB側 (adb_input.py)
- sendevent（raw touch event）使用
- ログ正規分布の反応遅延
- 2Dガウスジッター
- タッチ保持時間ランダム化
- 15%の迷いパターン

### GUI
- ウィンドウタイトル: "Network Diagnostics Tool"（タスクバー対策）
