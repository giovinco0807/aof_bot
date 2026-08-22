# CLAUDE.md

このリポジトリで作業するときに知っておくべきこと。

## 何のためのものか

**ポーカーの学習ツールで、賭博ではない。** PPPoker のプレイマネー卓が対象で、
現金は動かない。目的は勝つことではなく、自分の判断が最適解から何位ずれたかを
記録して振り返ること。

これは設計に効いている。**「勝ちには要らないが学習には要る」情報を捨てないこと。**
ソルバーが候補を全部返すのも、ソルバーが答えられない3人卓でも記録するのも、
断られた理由を `note` に残すのも、全部この理由。
「上位N件で十分」「解けない卓は記録しない」といった最適化を入れないこと。

## このリポジトリには2つのボットが入っている

| | |
|---|---|
| **AoF ボット** | `automation/` `hook/` — 既に動いている。All-in or Fold 用 |
| **OFC ボット** | `ofc/` — OFC (Pineapple) 用。開発中 |

**両者はフックスクリプト `hook/pppoker_hook.js` を共有する。**

## 壊してはいけないもの

- **`hook/packet_capture.py`（2935行）を改造しない。** 動いている AoF ボットの中核。
  OFC は別リーダー（`ofc/capture.py`）として実装してある。
  1プロセスで両方動かす場合は `ofc.capture.attach_to_capture()` が
  `PacketCapture` を**書き換えずに**ラップする
- **`hook/pppoker_hook.js` を改造しない。** 両ボットが依存している。
  OFC パケットは既に全てデコード済みで、足りないものは無い

## OFC ボットで作業するとき

まず `ofc/HANDOVER.md`（使い方・現状・リスク）と `ofc/README.md`（設計の理由）を読む。

### 変更前に必ず

```bash
python -m ofc.tests.test_ofc
```

231件。エンジンが無ければ208件（m3 のテストは自動スキップ）。
**変更後も必ず流す。** テストが落ちたまま完了と報告しない。

### 設計上の約束

- **`Advisor.feed()` は Frida のコールバックスレッドで走る。**
  ここで重い処理（solve、SQLite書き込み）をするとキャプチャが止まりパケットを落とす。
  状態更新とキュー投入だけに留めること
- **ソルバーは `ofc/solver.py` の契約経由で差し込む。** `ofc/` にストラテジを直接書かない
- **`legal_actions()` は全手が foul する局面では全手を返す。**
  空でないことを「foul しない」の保証と読まない
- **ソルバーは候補を全部返す。** 上位N件に切り詰めると採点機能（`recorder.py`）が
  「上位から外れた手」を評価できなくなる
- **カード表現の変換は `ofc/cards.py` だけで行う。** 3種類の encoding がある
  （wire int / 2文字テキスト / 0-51 code）

### 実行環境の制約

- **playing は Windows でしか動かない。** Frida が `PPPoker.exe` にアタッチし、
  配置ドラッグは `SetCursorPos` + `mouse_event` の win32 直呼び
- **テストと開発は Linux/macOS でもできる。** GUI と m3 のテストは自動スキップされる
- **エンジン（m3）は別リポジトリ。** `pineapple` の `codex/trainer-accounts` ブランチ。
  `OFC_REGULAR_ROOT` か `aof_bot` の隣に置く。`python -m ofc.install` が用意する
- **重みのピンはエンジン側が持っていて、モデル昇格のたびに動く。**
  「m3」は時期によって別物になる。`python -m ofc.main --show-pins` で今の中身を出す。
  読み込んだ重みの指紋を `decisions.engine` に記録しているので、
  **この記録を落とさないこと** — 指紋が無いと学習ログの EV ロスが
  「上達」なのか「相手が変わった」のか区別できなくなる

### git に入れないもの

`ofc/data/` 配下は全てマシン固有:

- `layout.json` — 配置座標。PC ごと・ウィンドウサイズごとの実測値
- `budget.json` — 思考時間の設定
- `ofc.db` — 記録データベース
- `m3engine.json` — エンジンの場所

## 自動配置を触るとき（最も危険な箇所）

`ofc/placer.py` は**実卓を実際に操作する唯一のコード**で、**実卓では未検証**。

- 拒否条件を緩めない。「拒否されない」と「正しく置く」は別物
- 座標は**ウィンドウ相対**で保存する。絶対座標にしない
- 中止手段は Ctrl-C とカーソル移動検知の2つ。
  **`pyautogui.FAILSAFE`（画面隅）は効かない** — ここは ctypes 直呼びなので。
  ドキュメントやメッセージにこれを書かない
- 変更したら `--dry-place` の出力が壊れていないか確認する

## コミット

作業ブランチは `claude/ofc-automation-bot-myd8aj`。
`git push -u origin claude/ofc-automation-bot-myd8aj`。
**指示がない限り Pull Request は作らない。**
