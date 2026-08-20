# OFC Bot — 使用書 / 引き継ぎ書

ローカル（自分の Windows PC）で動かすための手順書と、
このコードを引き継ぐ人が最初に読むべき現状report。

`README.md` は「なぜこの設計なのか」を書いたもの。
こちらは「どう動かすか」と「今どこまで出来ていて、何が危ないか」。

---

## 0. 一行で

PPPoker の OFC 卓のパケットを Frida で読み、盤面を再構築して、
学習済みエンジンに最適配置を尋ね、GUI に出す。
配置の自動実行（ドラッグ）も実装済みだが**既定はオフ**で、実卓では未検証。

---

## 1. 現状

### 動くと確認できているもの

| | 確認方法 |
|---|---|
| カード取得・盤面再構築 | 合成パケット・記録パケットの再生。テスト231件 |
| 役判定・ロイヤリティ・foul | pineapple 実装と5万回のランダム比較で完全一致 |
| m3 エンジン接続 | 実際にビルドして応答時間を実測（T0先行123ms / T1 40ms 等） |
| 思考時間の街別設定 | テスト |
| 記録・採点 | テスト。3人卓でも記録されることを含む |
| UID 自動特定 | 合成パケット6ケース |
| セットアップ一式 | まっさらな clone から通しで実行（エンジン clone → ビルド → テスト） |
| 配置の拒否条件 | フェイク win32 と合成レイアウトで各条件を発火させて確認 |

### 実卓で未検証のもの（重要）

**このコードは一度も実際の卓を操作していない。** 画面を見ないと分からない:

1. **手札の並び順がパケット順と一致するか** — 違えば毎回別のカードを掴む。**最重要**
2. カードが抜けたときストリップが詰まるか（右端から置く実装なのでどちらでも動くはずだが未確認）
3. スロットに落とせば必ずその位置に入るか（行末に追加されるだけかもしれない。OFC では行内順序は無意味なので実害は無いはず）
4. confirm ボタンの有無と挙動
5. 持ち時間切れでクライアントが自動配置した場合、こちらは古い盤面前提でドラッグする（検出機構なし）

1 は `--dry-place` で確認できる。**自動配置を使う前に必ず確認すること。**

---

## 2. セットアップ（Windows）

### 必ず Windows 側に置く。WSL では動かない

- フックが `PPPoker.exe`（Windowsプロセス）にアタッチする
- 配置ドラッグが win32 直呼び
- エンジンは `.dll` が要る。**WSL でビルドすると `.so` ができて Windows の Python からは読めない**

### 前提

- [Git](https://git-scm.com/download/win)
- [Python 3.9+](https://www.python.org/downloads/windows/)（インストール時に「Add to PATH」を入れる）
- [Rust](https://rustup.rs/) — エンジンのビルドに必要。入れたくなければ後述の `--no-engine`

### 手順

```powershell
cd C:\
git clone -b claude/ofc-automation-bot-myd8aj https://github.com/giovinco0807/aof_bot.git
cd aof_bot
python -m ofc.install
```

`ofc/install.py` が残りを全部やる:

1. エンジンのリポジトリを `C:\regular-ofc-pineapple` に clone
   （`main` には学習済みモデルが無い。`codex/trainer-accounts` ブランチ）
2. Rust ライブラリをビルド（1〜2分。一度だけ）
3. `frida` `frida-tools` `pygetwindow` を導入
4. テストスイートを流して結果を出す

**何度実行しても安全。** 済んだ段は飛ばす（ビルドをやり直さない）。
失敗したらその場で止まり、直し方を出す。

| オプション | 用途 |
|---|---|
| `--engine-root <path>` | エンジンを別の場所へ（既定は `aof_bot` の隣。隣なら設定不要で自動検出） |
| `--no-engine` | Rust 無しで `baseline` のみ（弱い。foul率59%） |
| `--no-packages` | pip を実行しない |
| `--with-aof` | AoF 側の OCR スタックも入れる（数GB。**OFC では一切使わない**） |

### 確認

```powershell
python -m ofc.main --list-solvers
```

```
registered solvers:
  baseline
  m3            ← これが出ればエンジンが読めている
```

`m3` が出ない場合は理由が併記される（見つからない／未ビルド／読み込み失敗）。

---

## 3. 日常の使い方

### 3.1 自分の UID を調べる（初回のみ）

`--hero-uid` は必須。どの席が自分か分からないと何も判断できない。
PPPoker を起動して OFC の卓に座った状態で:

```powershell
python -m ofc.main --discover
```

1ハンド配られた時点で UID を表示して終了する。

```
seats seen so far:
  seat 0  uid 111          alice
  seat 1  uid 222          bob

  YOUR UID IS 222
  (seat 1 was dealt As Ad Kh 7c 2d — nobody else's cards are ever sent
   to your client, so this is definitive)
```

**推測ではない。** `PineHandCardBRC` は全席を列挙するが、実際のカードが入っているのは
自分の entry だけ（他人の手札はクライアントに送られてこない）。
カードを持っている席＝自分、で確定する。3人卓でも4人卓でも曖昧にならない。

UID は PPPoker のプロフィール画面でも確認できる。

### 3.2 GUI で使う（推奨）

```powershell
python -m ofc.main --hero-uid <UID> --solver m3 --gui
```

2モードある。

**live** — Hero UID を入れて ATTACH。以降は自動追随。

- PPPoker がまだ起動していなくてもよい。**起動を待って自動でアタッチ**する
- 閉じて開き直しても**自動で再接続**する
- 盤面は自分の手番だけでなく、**相手が置いた時点でも更新**される
- 状態がヘッダーに出る（`waiting for the client` / `attached — following the table` / `client closed — waiting`）

候補一覧をクリックすると、その配置が盤面上に**黄色の枠**で重なる。

**manual** — ゲームを起動せずに局面を作って SOLVE。ソルバー開発や検討はこちらが速い。

52枚のカードピッカーをクリックして入力する:

1. 「Picked cards go to:」で入れ先を選ぶ（Dealt / Top / Mid / Bot / Opp）。各ボタンに現在の枚数が出る
2. ピッカーのカードをクリックするとそこに入る
3. **使用済みカードは打ち消し線**が付き、クリックで取り消せる
4. **盤面のカードを直接クリック**しても取り消せる
5. **盤面の空きスロットをクリック**すると入れ先がその行に切り替わる
6. `Undo` で1つ戻る

行の上限は超えられず、同じカードは二度選べないので、存在しない局面は入力できない。
`As Kd 7c` 形式のテキスト貼り付けも使える。

### 3.3 CLI で使う（GUI 無し）

```powershell
python -m ofc.main --hero-uid <UID> --solver m3
```

コンソールに毎ターンの推奨が出る。Ctrl-C で終了。

### 3.4 思考時間

ストリートごとに設定できる。初手は合法手232通り、以降は27通りなので探索量が桁違い。
1つの値で通すと初手が足りないか以降が余る。

既定: 初手6秒 / St1-2 3秒 / St3 2.5秒 / St4 2秒 / FL 15秒

```powershell
python -m ofc.main --show-budget                        # 現在の設定
python -m ofc.main --budget 5                           # 全街一律5秒
python -m ofc.main --budget-street 0=12 --budget-street 4=1
python -m ofc.main --budget-fantasyland 30
python -m ofc.main --ignore-table-clock                 # 卓の制限時間を無視
```

GUI 上部でも設定でき、`Save` で `ofc/data/budget.json` に保存される（git 管理外）。

**卓の制限時間で自動的に切り詰められる。** パケットの `actionLeftTime` を見て
`残り時間 − 2秒` まで縮む。時間切れになればクライアントが勝手に置くので、
間に合わない思考には意味がないため。

m3 は探索しない（順伝播1回）ので、実際にはこの予算をほとんど使わない。
予算が効くのは自作の探索型ソルバーを入れたとき。

---

## 4. 自動配置（Phase 2）— 段階を踏むこと

既定でオフ。**いきなり `--auto-place` を使わない。**

### 4.1 キャリブレーション

```powershell
python -m ofc.placer --calibrate    # 各位置にマウスを乗せて Enter
python -m ofc.placer --show         # 保存済みレイアウトの確認
```

13スロット（Top3 + Middle5 + Bottom5）＋**5枚用と3枚用の両方の手札位置**＋confirm を実測する。
1つでも欠けていれば実行を拒否する（推測は一切しない）。

**座標はウィンドウ相対で保存される。** ウィンドウを動かしても追随するが、
**サイズを変えたら測り直し**（クライアントが再レイアウトするため、拒否される）。

この座標は PC ごと・ウィンドウサイズごとの実測値なので **git に入っていない**。
環境を移したら測り直すこと。

### 4.2 dry-place で確認（必須）

```powershell
python -m ofc.main --hero-uid <UID> --solver m3 --dry-place
```

クリックせず、毎回のドラッグ手順だけを出す。

```
  [OFC place] dry run — nothing will be clicked:
    Ac -> bottom[2]  (100, 400) -> (200, 300)
    5s -> bottom[3]  (150, 400) -> (250, 300)
```

**確認すること:**

- [ ] `from` 座標が、実際にそのカードが表示されている位置と一致するか（**最重要**）
- [ ] `to` 座標が、狙った行の空きスロットか
- [ ] 置く枚数が正しいか（通常ストリートは3枚配られて2枚置く）
- [ ] 複数ハンド見て、毎回一致するか

`from` がズレていると**毎回違うカードを掴む**＝別のハンドをプレイしてしまう。
これが未検証項目の1番で、一番危ない。

### 4.3 実行

```powershell
python -m ofc.main --hero-uid <UID> --solver m3 --auto-place
```

### 4.4 中止する方法

- **Ctrl-C** — ドラッグとドラッグの間で確認されるので、進行中の配置にも届く
- **マウスを動かす** — ドラッグ中にカーソルが想定位置から40px以上ずれたら中止

**画面隅に動かしても止まらない。** あれは pyautogui の FAILSAFE で、
このコードは ctypes 直呼びなので効かない。

途中で止まった場合は「N/M 個目で停止、盤面は途中まで置かれている」と明示される。
その場合は手で置き切ること。

### 4.5 拒否される条件（すべて実行前に止まる）

- `enabled` が False / `request` 未指定
- レイアウト未完成（13スロット＋5枚用と3枚用の両方の手札位置）
- ウィンドウが見つからない／キャリブレーション時とサイズが違う
- **前面化できなかった**（OS に拒否された／最小化されたまま）
- **ドラッグの始点か終点がウィンドウの外に出る**
- Fantasyland（13枚同時配置。そのストリップは未計測）
- `hand_order` に無いカードを置こうとしている
- Windows 以外

拒否理由は必ずコンソールに出る。黙って何もしないことはない。

---

## 5. 記録と振り返り

**卓の人数に関係なく記録する。** m3 はヘッズアップ専用だが、3人卓のハンドも
同じゲームで同じミスが出る。今日解けない卓こそデータを取る価値がある。

記録先は `ofc/data/ofc.db`（SQLite、git 管理外）。

| テーブル | 内容 |
|---|---|
| `hands` | 完了ハンド1件1行。全員の最終盤面、foul、ロイヤリティ、FL入場 |
| `decisions` | 直面した局面1つ1行。盤面・配牌・デッドカード・**候補全部のランキング**・自分が実際に置いた手とその順位・EVロス |

ソルバーが答えられなかった局面も残り、`note` に理由が入る
（例: `the engine plays heads-up; this table has 2 opponents`）。
`seats` 列で人数を絞れる。

```powershell
python -m ofc.recorder --summary                      # 件数と平均EVロス（人数別・街別）
python -m ofc.recorder --mistakes 20                  # 損失の大きい判断を悪い順に
python -m ofc.recorder --mistakes 20 --seats 3 --street 1
```

```
2026-08-11T03:46:09  street 0  2 players  rank 53  cost 12.926
  board  T[-] M[-] B[-]
  dealt  As Ad Kh 7c 2d
  played 2d->top, Kh->middle, 7c->middle, As->bottom, Ad->bottom
  best   Kh->top, Ad->middle, As->middle, 2d->bottom, 7c->bottom
```

自分の着手は自動採点される。パケットは着手そのものを送ってこないので、
**置く前と後の盤面の差分**から復元している。

---

## 6. トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| `--hero-uid is required` | UID 未指定 | `python -m ofc.main --discover` |
| `PPPoker.exe is not running` | 未起動／プロセス名違い | 起動する。名前が違えば `--process <名前>` |
| `frida is not installed` | 依存未導入 | `python -m ofc.install` |
| Frida のアタッチが失敗 | 権限 | PowerShell を管理者で開き直す |
| `--list-solvers` に m3 が出ない | エンジン未ビルド／場所不明 | 併記された理由を読む。`python -m ofc.install` で解決することが多い |
| m3 が「.so を読めない」 | WSL でビルドした | Windows 側で `cargo build --release -p ofc_hu_m3_engine` |
| GUI が同期しない | UID 違い | `--discover` で取り直す |
| 3人卓で推奨が出ない | m3 はヘッズアップ専用 | 仕様。記録は取れているので後から分析できる |
| 推奨が出ない（2人なのに） | ジョーカー卓／パケット取りこぼし | 理由がコンソールに出る。誤った推奨より出さない方を選んでいる |
| 配置が毎回拒否される | 未キャリブレーション等 | 理由がコンソールに出る。§4.5 参照 |
| ハンド途中でアタッチした | 盤面不明 | `PineRoomStatusBRC` が届けば復元して続行。届かなければそのハンドは黙る |

---

## 7. 引き継ぎ

### 7.1 設計の要点

**ソルバー本体はこのパッケージに入っていない。** ここは受け皿で、
戦略ロジックは `ofc.solver.register()` で差し込む。
同梱の `m3` は pineapple の学習済みエンジンのプラグイン、
`baseline` は配線確認用（弱い。foul率59%）。

**OFC は公開情報ゲーム。** 相手が置いたカードは置いた瞬間に公開され、
パケットが即座に届ける。つまり `SolveRequest.deck` は推定ではなく
**本物の残りデッキ**（52 − 自分の盤面 − 自分の捨て札 − 相手の見えているカード全部）。
オフラインのソルバーには作れない情報なので、ここを使わない手はない。

**既存の `packet_capture.py`（2935行）は改造していない。** 動いている AoF ボットを
壊さないため、OFC は別リーダーとして実装した。フックスクリプトは共有で無改変。
1プロセスで両方動かしたい場合は `ofc.capture.attach_to_capture()` が
`PacketCapture` を書き換えずにラップする。

**アクションはマウスでしか実行できない。** `pppoker_hook.js:1148` に
`SendPacket crashes` と明記されている。だから配置はドラッグ。

**solve は絶対に Frida のコールバックスレッドでやらない。** そこで重い処理をすると
キャプチャが止まりパケットを落とす。`Advisor.feed()` は状態更新とキュー投入だけ。

### 7.2 ファイル責務

| | |
|---|---|
| `cards.py` | カード表現の変換。3種の encoding を扱う唯一の場所 |
| `evaluator.py` | 役の強さ、ロイヤリティ、foul、FL判定 |
| `board.py` | 3行の盤面 |
| `actions.py` | 合法手の列挙（foul枝刈り込み） |
| `state.py` | パケットから卓の状態を再構築 |
| `recorder.py` | ハンドと判断の記録・採点（人数不問） |
| `budget.py` | 街別の思考時間、卓の制限時間との突き合わせ |
| `solver.py` | **ソルバー契約とレジストリ** |
| `solvers/m3engine.py` | pineapple の学習済みエンジンを繋ぐプラグイン |
| `solvers/baseline.py` | 動作確認用のプレースホルダ |
| `advisor.py` | パケット → 状態 → ソルバー → イベント |
| `capture.py` | Frida 接続 |
| `discover.py` | 自分の UID をパケットから特定 |
| `install.py` | clone 後のセットアップ一式 |
| `gui.py` | 盤面と推奨配置の表示 |
| `placer.py` | 自動配置 |
| `replay.py` | オフライン再生・検証 |
| `tests/test_ofc.py` | テスト（231件） |

依存は標準ライブラリのみ。ライブ接続時のみ `frida`、自動配置時のみ `pygetwindow`。
**`pyautogui` は不要**（ドラッグもタップもウィンドウ探索も ctypes 直呼び）。

### 7.3 ソルバーを差し替える

```python
from ofc.solver import register, SolveRequest, Advice, Candidate

def my_solver(req: SolveRequest) -> Advice:
    actions = req.legal_actions()          # foul する手は除外済み
    scored = [Candidate(action=a, ev=my_score(a, req)) for a in actions]
    return Advice.of(req, scored, solver="mine")

register("mine", my_solver)
```

```powershell
python -m ofc.main --gui --solver-file path\to\my_solver.py --solver mine
python -m ofc.replay --synthetic 50 --solver mine
```

**注意:** `legal_actions()` は**全部の手が foul する局面では除外せず全手を返す**
（カードはどこかに置かねばならないため）。乱数生成した終盤局面では約半分で発生する。
返り値が空でないことを「foul しない」の保証と読まないこと。

**採点のために候補を全部返すこと。** 上位N件に切り詰めると、
学ぶ価値のある「上位から外れた手」がちょうど採点対象から漏れる。

### 7.4 テスト

```powershell
python -m ofc.tests.test_ofc
```

231件。エンジンが無ければ m3 のテストは自動スキップ（208件）。
Tk が無ければ GUI テストもスキップ。
pineapple が隣にあれば役判定を相互検証する。

### 7.5 未完了・既知の制限

- **実卓での配置が未検証**（§1 の5項目）。最優先
- **Fantasyland 非対応**。別クレート（`fl_solver_regular`）の管轄で、
  m3 エンジンからは到達できない。ストリップも未計測
- **m3 はヘッズアップ専用**。ただし判定は席数ではなく**参加人数**なので、
  3席卓でも実際に2人で回っていれば使える
- **T0後攻だけランキングが出ない**。エンジンの特徴量エンコーダが
  「空きスロット合計が2/4/6/8」を要求し、初手後攻が満たさないため。
  この局面だけ最善手のみ（`decide`）にフォールバック
- **ジョーカー非対応**。デコードできない値を検出して solve を止める
  （誤った推奨を出すより出さない方を選んでいる）
- **相手の捨て札は見えない**。仕様上そうなのでデッドカードに含まれない
- **返る `ev` はモデルのスコアで較正されたEVではない**。手の選択には使えるが
  「何点得か」としては読めない
- **`baseline` は弱い**（foul率59%）。差し替え前提

### 7.6 リスクの所在

**一番危ないのは自動配置。** 実卓を実際に操作する唯一の部分で、かつ未検証。
拒否条件は厚く積んであるが、「拒否されない」ことと「正しく置く」ことは別物。
手札の並び順の前提（§1-1）が外れていた場合、拒否条件はどれも発火せず、
**静かに間違ったハンドをプレイし続ける**。`--dry-place` での確認が唯一の防波堤。

2番目はパケット取りこぼし。Frida のコールバックで重い処理をしないよう
設計してあるが、記録の書き込みスレッドやソルバーのワーカーが詰まれば影響しうる。
`hands` テーブルの行合計が13枚にならないハンドが出たら、パケット読みがずれている
（`python -m ofc.replay --hands` で確認できる）。

---

### 7.7 ローカルで Claude Code を使って開発を続ける

### 課金の確認（最初にこれをやる）

Claude Code の課金経路は2つあり、**どちらで動いているかは見た目では分からない**。

| 経路 | 課金 |
|---|---|
| Claude アカウント（Pro / Max のサブスク）でログイン | サブスクに含まれる。従量課金は発生しない |
| Anthropic Console の API キー | **トークン従量課金**。使った分だけ請求される |

**一番よくある事故は `ANTHROPIC_API_KEY` が環境変数に残っていること。**
これが設定されていると、サブスクにログイン済みでも**API キー側が優先され**、
知らないうちに従量課金で動く。

確認手順:

```powershell
# 1. 環境変数が残っていないか
echo $env:ANTHROPIC_API_KEY        # 何も出なければOK

# 残っていたら、そのセッションだけ消す
Remove-Item Env:\ANTHROPIC_API_KEY

# 恒久的に消す（ユーザー環境変数から削除）
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", $null, "User")
```

```
# 2. Claude Code を起動して、どちらで動いているか確認
claude

/status      # 認証方法とアカウントが出る。サブスクならその旨が出る
/cost        # このセッションの課金状況
```

`/status` がサブスクではなく API キーを指している場合:

```
/logout
/login       # ブラウザが開くので Claude アカウントでログイン
```

**サブスクで動いていれば `/cost` は課金額を出さない**（従量課金ではないため）。
金額が出ている場合は API キー経由なので、上の手順で切り替える。

不安なら [console.anthropic.com](https://console.anthropic.com) の Usage を見る。
API キーを使っていなければ、ここに何も増えない。

### Claude Code がこのリポジトリを正しく扱うための準備

**`CLAUDE.md`（リポジトリ直下）を置いてある。** Claude Code は起動時にこれを読む。
中身は「壊してはいけないもの」「設計上の約束」「実行環境の制約」で、
これがないと以下のような事故が起きうる:

- 動いている AoF ボットの `packet_capture.py`（2935行）を「整理」してしまう
- `Advisor.feed()` に重い処理を足してキャプチャを止める
- 自動配置の拒否条件を「厳しすぎる」と判断して緩める
- 効かない中止方法（画面隅）をドキュメントに書き戻す

**変更したら消さないこと。** 引き継ぐ人が増えるほど効く。

### 毎回許可を聞かれないようにする

既定では Bash 実行やファイル編集のたびに確認が入る。頻繁に使うものは許可しておける:

```
/permissions
```

`.claude/settings.json` に保存される。
**このディレクトリは `.gitignore` に入っている**ので、マシンごとに設定する必要がある
（逆に言えば、あなたの許可設定が他人に押し付けられることはない）。

### 動作確認

```powershell
cd C:\aof_bot
claude

# 中で
/status                              # 認証と課金経路
> python -m ofc.tests.test_ofc を実行して      # 231件通れば環境は正常
```

テストが通り、`/status` がサブスクを指していれば、
**環境は正しく、課金もされていない**状態。

### 注意

- Claude Code の課金体系や画面表示は変わりうる。
  ここの記述より **`/status` と `/cost` の実際の出力**を信じること
- 実卓に接続した状態で Claude Code に自動配置を触らせない。
  検証は `--dry-place` で行う（§4.2）

---

## 8. コマンド早見表

```powershell
# セットアップ
python -m ofc.install                      # clone 後これ1つ
python -m ofc.main --list-solvers          # m3 が読めているか確認

# 使う
python -m ofc.main --discover                                  # UID を調べる
python -m ofc.main --hero-uid <UID> --solver m3 --gui          # GUI
python -m ofc.main --hero-uid <UID> --solver m3                # CLI

# 思考時間
python -m ofc.main --show-budget
python -m ofc.main --budget-street 0=12 --budget-street 4=1

# 自動配置（この順に）
python -m ofc.placer --calibrate
python -m ofc.main --hero-uid <UID> --solver m3 --dry-place
python -m ofc.main --hero-uid <UID> --solver m3 --auto-place

# 振り返り
python -m ofc.recorder --summary
python -m ofc.recorder --mistakes 20
python -m ofc.replay --hands

# 開発
python -m ofc.tests.test_ofc
python -m ofc.replay --synthetic 100 --solver mine
```

Claude Code で開発を続ける場合:

```powershell
echo $env:ANTHROPIC_API_KEY     # 空であること（残っていると従量課金になる）
claude
```

```
/status      # 認証経路の確認（サブスクか API キーか）
/cost        # 課金状況
/login       # サブスクに切り替える
/permissions # 毎回の確認を減らす
```

---

## 9. これまでの経緯（コミット）

| | |
|---|---|
| `af7b3ce` | UID をパケットから特定。中止方法の誤表示を修正 |
| `c7f6fbe` | clone 後のセットアップを1コマンドに |
| `b987fed` | 前面化が本当に成功したか確認してからドラッグする |
| `c9bfe3c` | 配置層を全面的に作り直し（ウィンドウ相対座標、右端から、等） |
| `e612e2c` | 推奨をドラッグに繋ぐ（引数不一致で100%拒否されていた）。`--dry-place` 追加 |
| `87daa0b` | 人数に関わらず記録する |
| `18671ab` | 相手の数を席ではなく参加人数で数える |
| `c6e2aa7` | クライアントの起動を待ち、手番外でも盤面を描く |
| `41af4a6` | m3 エンジンをプラグインとして接続 |
| `f80aee7` | 思考時間を街別に |
| `9345a61` | 盤面の枚数と街が合わない局面を拒否 |
| `25471c2` | GUI でカードをクリックして局面を作る |
| `98fd093` | パケット読み・手番追跡・配置安全性の修正 |
| `5839458` | 初版（パケット駆動の状態、ソルバー契約、盤面GUI） |
