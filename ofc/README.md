# OFC Bot

PPPoker の OFC（Pineapple）用ボット。カード取得は既存の AoF Frida フックを**そのまま流用**し、
この `ofc/` パッケージが「状態の記憶」「ソルバーの差し込み口」「盤面と推奨配置の表示」を担当する。

**これは OFC の学習ツールで、賭博ではない。** 対象は PPPoker のプレイマネー卓で、
現金は動かない。目的は勝つことではなく、**自分の判断が最適解から何位ずれたか**を
記録して振り返ること（`recorder.py`）。設計上の判断もそこから来ている —
ソルバーが候補を全部返すのも、3人卓でも記録するのも、
「勝ちに要らない情報」を捨てないためにそうしてある。

---

## セットアップ（Windows）

**必ず Windows 側に置くこと。WSL では動かない** — フックが `PPPoker.exe`（Windowsプロセス）に
アタッチし、配置ドラッグは win32 直呼びで、エンジンは `.dll` が要る（WSL でビルドすると
`.so` ができるが Windows の Python からは読めない）。

前提: [Git](https://git-scm.com/download/win) / [Python 3.9+](https://www.python.org/downloads/windows/) /
[Rust](https://rustup.rs/)（エンジンのビルドに必要。Rust を入れずに済ませたい場合は
下の `--no-engine` を使う。ただしソルバーは弱い `baseline` になる）

```powershell
cd C:\
git clone -b claude/ofc-automation-bot-myd8aj https://github.com/giovinco0807/aof_bot.git
cd aof_bot
python -m ofc.install
```

`ofc/install.py` が残り全部をやる: エンジンのリポジトリを `C:\regular-ofc-pineapple` に
clone（`main` には学習済みモデルが無いので `codex/trainer-accounts` ブランチ）→
Rust ライブラリをビルド → 依存パッケージを導入 → テストスイートを流して結果を出す。
**何度実行しても安全**で、済んでいる段は飛ばす（ビルドをやり直さない）。
どこかで失敗したら、その場で止まって直し方を出す。

| オプション | 用途 |
|---|---|
| `--engine-root <path>` | エンジンを別の場所に置く（既定は `aof_bot` の隣。隣なら設定不要で自動検出される） |
| `--no-engine` | Rust を入れずに `baseline` だけで動かす |
| `--no-packages` | pip を実行しない |
| `--with-aof` | AoF ボットの OCR スタックも入れる（数GB。**OFC では一切使わない**） |

導入するのは `frida` `frida-tools` `pygetwindow` の3つだけ。
`automation/requirements.txt` は既定では**使わない** — あれは AoF 側のもので、
`easyocr` が torch を引く。OFC はパケットからカードを読むので画素を1つも見ない。

### 自分の UID を調べる

`--hero-uid` は必須（どの席が自分か分からないと何も判断できない）。
PPPoker を開いて OFC の卓に座った状態で:

```powershell
python -m ofc.main --discover
```

1ハンド配られた時点で UID を表示して終了する。

**推測ではない。** `PineHandCardBRC` は全席を列挙するが、
**実際のカードが入っているのは自分の entry だけ**（他人の手札はクライアントに
送られてこない）。カードを持っている席＝自分、で確定する。
3人卓でも4人卓でも曖昧にならない。

**キャリブレーションだけは移せない。** スロット座標（`ofc/data/`）は
PC ごと・ウィンドウサイズごとの実測値なので git に入っていない。
自動配置を使う場合は `python -m ofc.placer --calibrate` を実行環境で行うこと。

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

## 同梱ソルバー

| 名前 | 中身 |
|---|---|
| `m3` | pineapple の regular-OFC プロジェクトの学習済みエンジン（後述）。**実戦用はこれ** |
| `baseline` | 配線確認用のプレースホルダ。foul率59%で弱い |

### `m3` — pineapple の学習済みエンジン

`ofc_hu_m3_engine`（Rust cdylib）と m7v5 ピンの重み14個を使う。ストリート別・席別の
学習済みモデルで、**探索なしの順伝播1回**で全候補をランキングする。

トレーナーの資料にある「初手20.5秒」は**教師探索**（232候補を全部ロールアウトして
ラベルを作る処理）の数字。モデルはその蒸留結果なので、打つ側は探索し直す必要がない。
4コア環境での実測:

| ストリート | 応答 | 候補数 |
|---|---|---|
| T0 先行 | 123 ms | 232（全ランキング） |
| T0 後攻 | 1.3 秒 | 1（下記） |
| T1 | 40 ms | 27 |
| T2 | 12 ms | 27 |
| T3 | 7 ms | 21 |

**準備**（regular-OFC プロジェクトのビルドが要る）:

```
cd <regular-ofc-repo>
cargo build --release -p ofc_hu_m3_engine
```

場所の指定は環境変数か設定ファイル:

```
set OFC_REGULAR_ROOT=D:\path\to\regular-ofc-pineapple
# または ofc/data/m3engine.json に {"root": "D:/path/to/regular-ofc-pineapple"}
```

必要なのは `target/release/` のライブラリと
`rust/hu_m3_engine/tests/fixtures/` の重み。ディレクトリには 20 個入っていて、
そのうち **14 個がピン留めされている**（残りは旧版）。

### どの重みを使っているか

ピン表はエンジン側（`trainer/engine_eval.py` の `WEIGHT_FILES`）が持っていて、
**モデルが昇格するたびに動く**。つまり「m3 エンジン」は時期によって別物になる。
今どれを読み込んでいるかは:

```
python -m ofc.main --show-pins
```

| スロット | ファイル |
|---|---|
| `t0_first` | `t0first_model_v1.bin` |
| `t0_second` | `t0_model_v1.bin` |
| `t1_first` | `t1first_model_v1.bin` |
| `t1_second` | `t1_model_v1.bin` |
| `t2_first` | `t2first_model_v2.bin` |
| `t2_second` | `t2_model_v2.bin` |
| `t3_first` | `t3first_model_v2.bin` |
| `t3_second` | `t3_model_v3.bin` |
| `t4` | `t4_model_v6.bin` |
| `fast_*` ×5 | `fast_{t0_second,t1_first,t1_second,t2_first,t2_second}_v1.bin` |

（`codex/trainer-accounts` の `e1e6aed` 時点。`fast_*` は内部ロールアウト用の軽量版）

**まだリポジトリに入っていないモデルを試す場合**は、重みを fixtures に置いて
`ofc/data/m3engine.json` でスロットを指名する。エンジン側のリポジトリを
編集しないので、`git pull` と喧嘩しない:

```json
{"weights": {"t2_second": "t2_model_v3x16.bin"}}
```

指名したスロットが存在しない、ファイルが無い、という場合は
**黙って既定に戻さず拒否する**。記録が「使ったつもりのモデル」と食い違うのを防ぐため。

### 記録にモデルの素性が残る

読み込んだ重み全部の sha から指紋を作り（例 `m3:c86e3e7fac91`）、
**判断1件ごとに `decisions.engine` に記録する**。
エンジンが入れ替われば指紋も変わるので、後から

```sql
SELECT engine, COUNT(*), AVG(ev_loss) FROM decisions GROUP BY engine;
```

で切り分けられる。**指紋の違う行同士の EV ロスは比較できない** —
採点した相手が別物だから。学習ログとしてはここが生命線で、
指紋が無いと「上達したのか、相手が変わったのか」が永久に分からなくなる。

**制限**（該当時は理由付きで推奨を出さずに黙る）:

- **ヘッズアップ専用。** ただし判定は「席数」ではなく「そのハンドに参加している人数」。
  3席卓でも実際に2人で回っていればヘッズアップとして使える
  （空席・sitting out・ハンド途中の着席は相手として数えない。
  `PineGameStartBRC.startInfo` が配られた席の決定的な情報源）
- **Fantasyland 非対応。** 別クレート（`fl_solver_regular`）の管轄で、
  このエンジンからは到達できない
- **T0後攻だけランキングが出ない。** エンジン内部の特徴量エンコーダが
  「盤面の空きスロット合計が2/4/6/8」を要求し、初手後攻はこれを満たさない。
  この局面だけ `decide`（最善手のみ）にフォールバックする
- 盤面の枚数がストリートと合わない、自分の捨て札が欠けている、といった場合も拒否する。
  エンジンが形状を検証するため（パケット取りこぼしの検出にもなる）
- 返る `ev` は**モデルのスコアで、較正されたEVではない**。手の選択には使えるが、
  「何点得か」としては読めない

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
| `req.deadline` | 打ち切るべき時刻（`time.monotonic()` 基準） |
| `req.time_left()` | 残り秒数 |

探索型のソルバーは `req.time_budget` を秒数として使うか、
`while time.monotonic() < req.deadline:` で回す。
`time_budget` は**卓の残り時間で既に切り詰められている**ので、
そのまま使えば時間切れになることはない。

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
- **live** — Hero UID を入れて ATTACH。以降は自動で追随する。
  - PPPoker がまだ起動していなくてもよい。**起動を待って自動でアタッチ**する
  - PPPoker を閉じて開き直しても**自動で再接続**する
  - 盤面は自分の手番だけでなく、**相手が置いた時点でも更新**される
  - 状態はヘッダーに出る（`waiting for the client` / `attached — following the table` /
    `client closed — waiting`）

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

### 思考時間（各ターン別に設定）

ストリートごとに秒数を設定できる。初手は合法手 232 通りで以降は 27 通りなので、
探索量が桁違いになる。1 つの値で通すと初手が足りないか、以降が余る。

GUI 上部の「Thinking time per turn」で設定し、`Save` で保存される
（`ofc/data/budget.json`。マシン固有なので git 管理外）。

CLI でも指定できる:

```
python -m ofc.main --show-budget                       # 現在の設定を表示
python -m ofc.main --budget 5                          # 全ストリート一律 5 秒
python -m ofc.main --budget-street 0=12 --budget-street 4=1
python -m ofc.main --budget-fantasyland 30
python -m ofc.main --ignore-table-clock                # 卓の制限時間を無視
```

既定値は 初手 6 秒 / St1-2 3 秒 / St3 2.5 秒 / St4 2 秒 / FL 15 秒。

**卓の制限時間で自動的に切り詰められる。** パケットの `actionLeftTime` に
持ち時間が入っているので、それより長い予算を設定しても
`残り時間 − 2 秒`（配置操作と通信の余裕）まで縮む。時間切れになれば
クライアントが勝手に置いてしまうので、間に合わない思考には意味がないため。
`actionLeftTime` の単位は未確認なので、秒として不自然な値（1〜600 の範囲外）は
信用せず無視する。この挙動はチェックボックスで切れる。

ソルバーが予算を大幅に超過した場合はログに出る。

### 記録（人数に関わらず全部残す）

**卓の人数に関係なく記録する。** 強いソルバー（`m3`）はヘッズアップ専用だが、
3人卓のハンドも同じゲームで同じミスが出る。むしろ今日解けない卓こそ
データを取っておく価値がある。

記録先は `ofc/data/ofc.db`（SQLite、マシン固有なので git 管理外）。
テーブルは2つ:

| テーブル | 内容 |
|---|---|
| `hands` | 完了したハンド1件につき1行。全員の最終盤面、foul、ロイヤリティ、FL入場 |
| `decisions` | 自分が直面した局面1つにつき1行。盤面・配牌・デッドカード・**候補全部のランキング**・そして自分が実際に置いた手とその順位・EVロス |

`decisions` には**ソルバーが答えられなかった局面も残る**。
そのとき `note` に理由が入る（例: `the engine plays heads-up; this table has 2 opponents`）。
`seats` 列で人数を絞れるので、3人卓のデータだけ後から取り出せる。

自分の着手は自動で採点される。パケットは着手そのものを送ってこないので、
**置く前と後の盤面の差分**から復元し、候補ランキングの何位だったか・
ベストとのEV差はいくつかを記録する。

```
python -m ofc.recorder --summary            # 件数と平均EVロス（人数別・ストリート別）
python -m ofc.recorder --mistakes 20        # 損失の大きい判断を悪い順に
python -m ofc.recorder --mistakes 20 --seats 3 --street 1
```

`--mistakes` の出力例:

```
2026-08-11T03:46:09  street 0  2 players  rank 53  cost 12.926
  board  T[-] M[-] B[-]
  dealt  As Ad Kh 7c 2d
  played 2d->top, Kh->middle, 7c->middle, As->bottom, Ad->bottom
  best   Kh->top, Ad->middle, As->middle, 2d->bottom, 7c->bottom
```

書き込みは専用スレッド。パケット処理は Frida のコールバックスレッドで走るので、
そこで SQLite に書くとキャプチャが止まる。
`Advisor(record=False)` で無効化できる。

**採点にはソルバーが候補を全部返す必要がある。** 上位N件に切り詰めると、
学ぶ価値のある「上位から外れた手」がちょうど採点対象から漏れる。
同梱ソルバーは両方とも全候補を返す。

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

# 実卓に繋いで、クリックせずに毎回のドラッグ手順だけ出す（推奨: これで先に確認する）
python -m ofc.main --hero-uid <UID> --solver m3 --dry-place

# 納得してから初めて
python -m ofc.main --hero-uid <UID> --solver m3 --auto-place
```

`--dry-place` の出力例:

```
  [OFC place] dry run — nothing will be clicked:
    Ac -> bottom[2]  (100, 400) -> (200, 300)
    5s -> bottom[3]  (150, 400) -> (250, 300)
```

座標が 1 つでも未計測なら実行を拒否する。
測っていない座標へのドラッグは実卓では事故になるため、推測は一切しない。
`execute()` は以下をすべて拒否する:

- `enabled` が False / `request` 未指定
- レイアウト未完成（3行13スロット＋**5枚用と3枚用の両方**の手札位置）
- **ウィンドウが見つからない**、または**キャリブレーション時とサイズが違う**
- **前面化できなかった**（OS に拒否された／最小化されたまま）
- **ドラッグの始点か終点がウィンドウの外に出る**
- Fantasyland（13枚同時配置。そのストリップは計測していない）
- `hand_order` に無いカードを置こうとしている
- Windows 以外（ドラッグは `SetCursorPos`+`mouse_event`）

**座標はウィンドウ相対で保存する。** 絶対座標だとウィンドウを動かした瞬間に
全ドラッグがズレるうえ、ズレたことはドラッグ側からは見えない。
実行時にウィンドウを取り直して変換し、サイズが変わっていれば
（クライアントは再レイアウトするので）拒否する。

**ドラッグはストリップの右端から順に行う。** カードが抜けたときに
クライアントが詰める場合、右から取れば残りの位置が動かない。
詰めない場合も順序は無害。行内の順序は OFC では意味を持たない。

**前面化は「頼む」だけでは足りない。** Windows は、フォアグラウンドを
持っていないプロセスからの `SetForegroundWindow` を**黙って拒否する**
（戻り値だけ見ても成功と区別がつかない）。そこで:

- 最小化されていれば `SW_RESTORE` してから頼む
- 頼んだ後に `GetForegroundWindow()` を読み返し、実際に前面に来たか確認する
- 来ていなければ「上に載っているものにドラッグするところだった」として拒否
- **ドラッグとドラッグの間でも再確認する**（途中で前面を奪われうる。
  半分だけ置かれた盤面は、1枚も置かれていない盤面より悪い）。
  既に前面なら待たずに即通すので、通常時のコストはゼロ

**全ドラッグ点がウィンドウ矩形の内側にあることも事前に確認する。**
1点でも外に出るなら、その時点で（1回もドラッグせずに）拒否する。

**中断について。** 「マウスを画面隅に動かせば止まる」は**誤りだったので削除した** —
`pyautogui.FAILSAFE` は pyautogui 経由の操作にしか効かず、ここは ctypes 直呼び。
代わりに実際に効く手段を2つ入れた:

- ドラッグ中にカーソルが想定位置から 40px 以上ずれたら「誰かが触った」と判断して中止
- ドラッグとドラッグの間で停止要求を確認する（Ctrl-C が進行中の配置にも届く）

途中で止まった場合は「N/M 個目で停止、盤面は途中まで置かれている」と明示する。

`hand_order` は**画面上の手札の左からの並び順**で、必須。省略できない理由は、
ソルバー出力順で代用すると「置き先の順」で手札を掴むことになり、
**毎回違うカードをドラッグする**（別のハンドをプレイしてしまう）ため。
通常は `request.dealt`（パケット順）を渡す。

`board` 引数には配置前の自分の盤面を渡す。行は左から埋まるので、
これが無いと初手以外は既に埋まっているスロットを狙ってしまう。

### 実卓で未検証の前提（`--auto-place` の前に必ず確認）

**このコードは一度も実際の卓を操作していない。** 以下は画面を見ないと分からない:

1. **手札の並び順がパケット順と一致するか。** 違えば毎回別のカードを掴む。
   `--dry-place` で出る `from` 座標と実画面を突き合わせること
2. **カードが抜けたときストリップが詰まるか。** 右端から順に置く実装なので
   どちらでも動くはずだが、確認していない
3. **スロットに落とせば必ずその位置に入るか**（行末に追加されるだけの実装かもしれない）。
   OFC では行内の順序は無意味なので実害は無いはずだが、未確認
4. **confirm ボタンの有無と挙動**
5. **持ち時間切れでクライアントが自動配置した場合**、こちらは古い盤面を前提に
   ドラッグしてしまう。現状これを検出する仕組みは無い

---

## ファイル構成

| | |
|---|---|
| `cards.py` | カード表現の変換。3 種の encoding を扱う唯一の場所 |
| `evaluator.py` | 役の強さ、ロイヤリティ、foul、FL 判定 |
| `board.py` | 3 行の盤面 |
| `actions.py` | 合法手の列挙（foul 枝刈り込み） |
| `state.py` | パケットから卓の状態を再構築 |
| `recorder.py` | ハンドと判断の記録・採点（人数不問） |
| `budget.py` | ストリート別の思考時間、卓の制限時間との突き合わせ |
| `solver.py` | **ソルバー契約とレジストリ** |
| `solvers/m3engine.py` | pineapple の学習済みエンジンを繋ぐプラグイン |
| `solvers/baseline.py` | 動作確認用のプレースホルダ |
| `advisor.py` | パケット → 状態 → ソルバー → イベント |
| `capture.py` | Frida 接続 |
| `gui.py` | 盤面と推奨配置の表示 |
| `placer.py` | 自動配置（Phase 2） |
| `replay.py` | オフライン再生・検証 |
| `discover.py` | 自分の UID をパケットから特定 |
| `install.py` | clone 後のセットアップ一式 |
| `tests/test_ofc.py` | テスト |

依存は Python 標準ライブラリのみ。ライブ接続時のみ `frida`、
自動配置時のみ `pygetwindow`（ウィンドウ矩形の取得に使う）。

**`pyautogui` は不要。** ドラッグもタップも `SetCursorPos` + `mouse_event` の
ctypes 直呼びで、ウィンドウハンドルの取得も `EnumWindows`。
`pc_input.py` は pyautogui が無ければ `None` にフォールバックする。
（この結果、pyautogui の `FAILSAFE`（画面隅で中止）は**効かない**。
中止手段は Ctrl-C とカーソル移動検知の2つ。上記「中断について」を参照）

---

## 既知の制限

- **ジョーカー非対応。** `cards.wire_to_text()` はデコードできない値を `''` にし、
  `state` がそれを検出して solve を止める。ジョーカー卓では推奨が出ない
  （誤った推奨を出すよりは、出さない方を選んでいる）。
- **ハンド途中でのアタッチ**は、`PineRoomStatusBRC` / `PineSitDownBRC` が届けば
  全員の盤面・自分の捨て札まで復元して続行できる（これらのパケットは
  `PineCard` を積んでいる）。届かない場合は盤面が不明なので、
  そのハンドの間は推奨を出さない。3枚ストリートは盤面が 5/7/9/11 枚のときしか
  成立しないので、0 枚の幻の盤面に対して助言することはない。
- **`baseline` は弱い。** foul 率 59%。差し替え前提。
- **相手の捨て札は見えない。** 仕様上そうなっているので、
  デッドカードには含まれない。
- **手札の並び順は要検証**（上記 Phase 2 参照）。
