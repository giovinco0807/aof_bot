# AoF Bot — AI 機能まとめ

## 1. パケットキャプチャ & データ収集

### `hook/packet_capture.py` + `hook/pppoker_hook.js`
PPPoker の通信を Frida で傍受し、全ハンドを自動記録。

- **Frida フック**: `GameAssembly.dll` の `OnDispatchPacket` をインターセプト
- **パケット解析**: ActionBRC, RoundStartBRC, ShowHandRSP, WinnerRSP 等を解読
- **ポジション判定**: ActionBRC の到着順（`action_order`）から CO/BTN/SB/BB を正確に割り当て
- **データ保存**: `hands.db` に全ハンド・アクション・カード・損益を記録

```
python hook/packet_capture.py --hero-uid <UID>
```

---

## 2. GTO チャート（HRC）

### `solver/data/charts_rb50/`

**HRC（Holdem Resources Calculator）** で計算した GTO チャート。自前 Rust CFR は 2P では収束するが、3〜4人マルチウェイオールインのエクイティ計算が複雑で収束しなかったため、HRC を使用。

- `aof_2p_8bb.json`, `aof_3p_8bb.json`, `aof_4p_8bb.json`
- 169 ハンド × 全状況（ポジション × 先行アクション）の allin_freq
- レーキ込み（rb50 = rake basis 50）

### Rust ソルバー: `solver/src/solver.rs`
- CFR 実装は存在するが、マルチウェイ収束問題のため GTO 計算には不使用
- エクイティテーブル（169×169）の事前計算、API サーバーとして稼働

---

## 3. 搾取エンジン（Rust）

### `solver/src/exploit.rs` + `solver/src/opponent_model.rs`

Ganzfried & Sandholm (2015) "Safe Opponent Exploitation" ベース。

- **ベイジアン対戦相手モデル**: 各プレイヤーの push rate を状況別に学習
- **Safe Exploitation**: GTO からの乖離を信頼度に比例して制限
  - ルースな相手 → push レンジを絞る（相手が call しすぎるので）
  - タイトな相手 → push レンジを広げる（相手が fold しすぎるので）
- **ブレンド戦略**: `blended_allin = (1 - confidence) × GTO + confidence × exploit`
- **API エンドポイント**: `/exploit?hand=AKs&num_players=4&player_ids=...`
- **制限**: マルチウェイ（3+人オールイン）のエクイティはペアワイズ近似。発生頻度が低いため実用上問題なし

---

## 4. リアルタイムアドバイザー

### `hook/packet_capture.py` 内 `_check_solver_advice()`

プレイ中にリアルタイムで GTO / Exploit 判定を表示。

```
>>> EXPLOIT: AKs | 4p 8BB | prior=AF |
    GTO=85.0% Exploit=92.3% Blend=89.1% conf=45% -> AllIn <<<
```

- **ActionBRC 到着時** にheroの番か判定 → ソルバーAPI に問い合わせ
- **自動プレイモード**: `--auto-play` で ADB 経由で Android 端末をタップ操作

---

## 5. カード認識

### `automation/card_reader.py` + `automation/card_recognizer.py`

Android 画面のスクリーンショットからカードを識別。

- **ハッシュベース認識**: カード画像の perceptual hash を DB と照合
- **学習パイプライン**: 未知のカードを自動学習・ハッシュDB更新
- **テンプレートマッチング**: OpenCV でカード領域を検出

---

## 6. 対戦相手プロファイリング

### `analyze_gto_dev.py` — ポジション別 push rate 分析
各プレイヤーの状況別 push rate を GTO と比較して傾向を判定。

```
Player 13386305  |  142 situations  |  Overall VPIP: 38.0%
  4P CO prior=''    52.0%  (GTO 29.1%)  +22.9%  ⚠ VERY LOOSE
```

### `analyze_mistakes.py` — ハンド別ミス分析
ショーダウンカードから個別ハンドの GTO 乖離を特定。

```
13386498:  4P BTN prior='A'  Q8o → PUSH (GTO: fold)
```

出力: `automation/data/player_mistakes.json`

---

## 7. 自動テーブル管理

### `hook/packet_capture.py` 内
- **自動入室**: ClubRoomRSP でテーブルリスト監視 → 2人以上で自動参加
- **自動退出**: 最大ハンド数、指定時刻、不利テーブル検出で自動退出
- **OpenCV テーブルクリック**: テンプレートマッチングで UI 操作

---

## アーキテクチャ

```
Android 端末 (PPPoker)
    ↕ ADB (画面取得 / タップ)
PC (Windows)
    ├── Frida Hook → パケット傍受
    ├── packet_capture.py → データ収集 + リアルタイム判定
    ├── Rust Solver API (localhost:8080)
    │   ├── /gto → GTO ルックアップ
    │   ├── /exploit → 搾取判定
    │   └── /record_hand → ベイジアンモデル更新
    └── hands.db → ハンド履歴
```
