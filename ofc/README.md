# OFC Bot

PPPoker の OFC（Pineapple）用ボット。カード取得は既存の AoF Frida フックを**そのまま流用**し、
この `ofc/` パッケージが「状態の記憶」「ソルバーの差し込み口」「盤面と推奨配置の表示」を担当する。

**ソルバー本体はこのパッケージには入っていない。** ここにあるのは受け皿だけで、
戦略ロジックは `ofc.solver.register()` で差し込む。同梱の `baseline` は配線確認用の
プレースホルダで、強くない（実測 foul 率 59%）。

---

## なぜこの構成なのか

調査して分かった事実が設計を決めた。

**カード取得は既に完成している。** `hook/pppoker_hook.js` は `Pine*` パケットを
すべてデコード済みで、自分の配牌・全員の配置行・ショーダウンが揃って届く。
画面 OCR は一切不要。ただし `hook/packet_capture.py` の OFC ハンドラは
**取得したカードを捨てていた**（`_on_pine_hand_card` は print するだけ）。
この受け皿はそこを引き取る。

**OFC は公開情報ゲームである。** 相手が置いたカードは置いた瞬間に公開情報になり、
パケットは即座にそれを届ける。つまり `SolveRequest.deck` は推定ではなく
**本物の残りデッキ**（52 − 自分の盤面 − 自分の捨て札 − 相手の見えているカード全部）。
オフラインのソルバーには絶対に作れない情報で、ここを使わない手はない。

**pineapple 側の AI はそのままでは使えない。** 学習済みモデルが 1 つも無く
（`.pt`/`.pth` がリポジトリに存在しない）、Rust ソルバーも未ビルド、
`ai/mcts/mcts.py` は木が深くならない未完成実装。唯一動く torch フリーの
`heuristic_score` は **foul 判定を一切しない**。だから流用先は
「AI」ではなく「役とロイヤリティのルール」だけにした。

**アクションはマウスでしか実行できない。** `pppoker_hook.js:1148` に
`SendPacket crashes` と明記されている。AoF ボットが Fold/AllIn を画面クリックで
押しているのと同じ理由で、OFC の配置もドラッグで行うしかない（Phase 2）。

**既存の `packet_capture.py`（2935 行）は改造していない。** 動いている AoF ボットを
壊さないため、OFC は別リーダーとして実装した。フックスクリプト自体は共有で無改変。
1 プロセスで両方動かしたい場合は `ofc.capture.attach_to_capture()` が
`PacketCapture` を**書き換えずに**ラップする。

---

## ソルバーの書き方

これが本体。`SolveRequest` を受けて `Advice` を返す関数を書いて登録するだけ。

```python
from ofc.solver import register, SolveRequest, Advice, Candidate

def my_solver(req: SolveRequest) -> Advice:
    actions = req.legal_actions()          # foul する手は既に除外済み
    scored = [Candidate(action=a, ev=my_score(a, req)) for a in actions]
    return Advice.of(req, scored, solver="mine")

register("mine", my_solver)
```

実行:

```
python -m ofc.main --gui --solver-file path/to/my_solver.py --solver mine
python -m ofc.replay --synthetic 50 --solver mine
```

### 入力 `SolveRequest`

カードはすべて `code` 整数（0-51、`rank * 4 + suit`）。`ofc.cards` で変換する。

| フィールド | 内容 |
|---|---|
| `board` | 自分の現在の盤面（`Board`、`.top` `.middle` `.bottom`） |
| `dealt` | 今置くカード。5枚=初手 / 3枚=通常ストリート / 13枚以上=FL |
| `street` | 0=初手、1以降=通常ストリート |
| `discards` | 自分が今までに捨てたカード（自分にしか見えない） |
| `opponents` | `OpponentView` のリスト。`.board` は相手の見えている行 |
| `in_fantasyland` | FL 中か |
| `time_budget` | 使ってよい秒数 |

派生プロパティ（計算済み）:

| | |
|---|---|
| `req.deck` | **本物の残りデッキ**。ここからサンプリングする |
| `req.dead_cards` | 見えているカード全部（重複排除済み、`len(dead)+len(deck)==52`） |
| `req.legal_actions()` | 合法手。foul 確定の手は除外 |

`legal_actions()` の注意: **全部の手が foul する局面では、除外せず全手を返す**
（カードはどこかに置かねばならないため）。乱数生成した終盤局面では約半分で発生する。
返り値が空でないことを「foul しない」の保証と読まないこと。必要なら
`action.apply(board).is_foul()` で確認する。

### 出力 `Advice`

`Candidate(action, ev, detail)` のリスト。`ev` はそのソルバー内での相対順位づけに
使うだけなので、単位は何でもよい（点数でもロイヤリティでも）。
`detail` は自由な dict で、GUI がそのまま表示する（`foul_rate`、`rollouts` など）。
`ev` / `placements` / `discard` と同名のキーは `detail_` 接頭辞を付けて退避するので、
診断情報が配置内容を壊すことはない。

`Advice.of(req, candidates, solver=...)` を使えば `ev` 降順に並べてくれる。

`Action` の捨て札は、通常ストリートは `discard`（1枚）、
FL は `discards`（複数枚）。`action.mucked` がどちらの場合も全捨て札を返す。

### 使える道具

```python
from ofc import evaluator as ev

ev.eval5(codes)                    # 5枚の強さ（int、大きいほど強い）
ev.eval3(codes)                    # 3枚。eval5 と同じ尺度なので直接比較できる
ev.is_foul(top, middle, bottom)
ev.total_royalty(top, middle, bottom)
ev.fantasyland_entry(top)          # 0 / 14 / 15 / 16 / 17
ev.fantasyland_stay(top, bottom)
ev.compare_boards(t1,m1,b1, t2,m2,b2)   # 相手1人に対する獲得点（scoop/foul込み）
```

`eval5` は 39万ハンド/秒。pineapple の実装と 5 万回のランダム比較で完全一致を確認済み
（テストに含まれている）。

`eval3` を `eval5` と同じ尺度に載せてあるのは意図的で、
top AAA が middle 222 に勝つ、を正しく扱うため。pineapple の
「3枚トリップスは一律 2.5」は、この比較を誤る。

### 守られること

`ofc.solver.validate()` が実行前に必ず走る:

- 配られていないカードを置いていないか
- 同じカードを 2 回使っていないか
- 行の枚数を超えていないか
- 置く枚数が正しいか

これらに引っかかった手は **auto-place で実行されない**。
foul は `warnings` 側で、実行はブロックしない（全部の手が foul することがあるため）。

ソルバーが例外を投げても捕まえて `Advice.note` に入れるので、キャプチャは落ちない。

---

## 使い方

### GUI（Phase 1）

```
python -m ofc.main --gui
```

2 つのモードがある。

- **manual** — ゲームを起動せずに局面を作って SOLVE。ソルバー開発はこれが一番速い。
- **live** — Hero UID を入れて ATTACH。実際の卓に追随する。

候補一覧をクリックすると、その配置が盤面上に**黄色の枠**で重ねて描かれる。

### 局面の作り方（manual）

52 枚のカードピッカーをクリックして入力する。

1. 「Picked cards go to:」で入れ先を選ぶ（Dealt / Top / Mid / Bot / Opp）。
   各ボタンには現在の枚数が出る（`Top (1/3)` など）
2. ピッカーのカードをクリックすると、その入れ先に入る
3. **既に使ったカードは打ち消し線が付き**、クリックすると取り消せる
4. **盤面のカードを直接クリック**しても取り消せる
5. **盤面の空きスロットをクリック**すると、入れ先がその行に切り替わる
6. `Undo` は 1 つ前の選択を戻す

行の上限（Top 3 枚など）は超えられず、同じカードは二度選べないので、
存在しない局面は入力できない。

テキスト入力も残してあるので、`As Kd 7c` 形式での貼り付けも使える
（打ち込んだ場合もピッカーの打ち消し線に反映される）。
テキストで重複を作った場合は SOLVE 時に弾かれる。

### オフライン検証

```
python -m ofc.replay --synthetic 100 --solver mine     # 乱数で作った局面
python -m ofc.replay --packets automation/data/packets.db --hero-uid <UID>
python -m ofc.replay --hands  automation/data/hands.db  # 記録済みハンドの集計
```

`--packets` は記録済みパケットを再生して、実戦と同じ順番で同じ質問をソルバーに投げる。
`--hands` は `ofc_hands` テーブルを集計して foul 率・ロイヤリティ・FL 率を出す
（キャプチャ自体の健全性チェックにもなる — 行の合計が 13 枚にならなければ
パケット読みがずれている）。

### テスト

```
python -m ofc.tests.test_ofc
```

pineapple リポジトリが隣にあれば、役判定とロイヤリティを相互検証する。
GUI のテストは Tk が使えない環境では自動でスキップされる。

### ヘッドレス

```
python -m ofc.main --hero-uid 12345678 --solver mine
```

---

## 自動配置（Phase 2）

既定で**オフ**。`--auto-place` で明示的に有効化する。

OFC の配置はドラッグ操作で、`PcController` にはタップしか無い。
そこで `ofc/placer.py` が人間らしい曲線ドラッグを実装し、
13 スロット＋手札位置の座標をキャリブレーションで実測して保存する。

```
python -m ofc.placer --calibrate    # 各位置にマウスを乗せて Enter
python -m ofc.placer --show         # 保存済みレイアウト
python -m ofc.placer --dry-run      # クリックせずに手順だけ表示
```

座標が 1 つでも未計測なら `Placer.readiness()` が実行を拒否する。
測っていない座標へのドラッグは実卓では事故になるため、推測は一切しない。
同様に `execute()` は以下をすべて拒否する:

- `enabled` が False
- レイアウト未完成（手札 5 枚分の位置が必要 — 初手は 5 枚同時に置くため）
- `hand_order` 未指定
- `hand_order` に無いカードを置こうとしている

`hand_order` は**画面上の手札の左からの並び順**で、必須。省略できない理由は、
ソルバー出力順で代用すると「置き先の順」で手札を掴むことになり、
**毎回違うカードをドラッグする**（別のハンドをプレイしてしまう）ため。
通常は `request.dealt`（パケット順）を渡す。

`board` 引数には配置前の自分の盤面を渡す。行は左から埋まるので、
これが無いと初手以外は既に埋まっているスロットを狙ってしまう。

**未検証の前提**: 手札の並び順がパケット順と一致するか。実画面で必ず確認すること。

---

## ファイル構成

| | |
|---|---|
| `cards.py` | カード表現の変換。3 種の encoding を扱う唯一の場所 |
| `evaluator.py` | 役の強さ、ロイヤリティ、foul、FL 判定 |
| `board.py` | 3 行の盤面 |
| `actions.py` | 合法手の列挙（foul 枝刈り込み） |
| `state.py` | パケットから卓の状態を再構築 |
| `solver.py` | **ソルバー契約とレジストリ** |
| `solvers/baseline.py` | 動作確認用のプレースホルダ |
| `advisor.py` | パケット → 状態 → ソルバー → イベント |
| `capture.py` | Frida 接続 |
| `gui.py` | 盤面と推奨配置の表示 |
| `placer.py` | 自動配置（Phase 2） |
| `replay.py` | オフライン再生・検証 |
| `tests/test_ofc.py` | テスト |

依存は Python 標準ライブラリのみ。ライブ接続時のみ `frida`、
自動配置時のみ `pyautogui`（`automation/requirements.txt` に既にある）。

---

## 既知の制限

- **ジョーカー非対応。** `cards.wire_to_text()` はデコードできない値を `''` にし、
  `state` がそれを検出して solve を止める。ジョーカー卓では推奨が出ない
  （誤った推奨を出すよりは、出さない方を選んでいる）。
- **`baseline` は弱い。** foul 率 59%。差し替え前提。
- **相手の捨て札は見えない。** 仕様上そうなっているので、
  デッドカードには含まれない。
- **手札の並び順は要検証**（上記 Phase 2 参照）。
