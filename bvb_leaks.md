## SB vs BB (ブラインド対決) 限定のエクスプロイト解析

後ろにまだアクションが残っている他プレイヤーが存在しない「純粋な1対1（ヘッズアップ状態）」のシチュエーションのみを抽出し、誰からの搾取が安全かつ最大利益を生むかを分析しました。

> [!TIP]
> * **SBの先制Push**: 相手（SB）が過剰にPushしてくる（🟥）なら、こちらはBBで広くコールしてキャッチ可能。タイトすぎる（🟩）なら本来降りる手でもコールせず降りて搾取を防ぐ。
> * **BBのCall**: 相手（BB）がルースにコールしすぎる（🟥）なら、こちらはSBからの「弱い手でのブラフPush」をやめる。タイトに降りすぎる（🟩）なら、こちらはSBから100%（エニハン）でPushしてブラインドを盗みまくる。

### 1. **キングジャック** (`13082001`) - 5,513 Hands
* **SBの先制Push (対BBのエクスプロイト)** : <span style='color: red'>**🟥 Over-Bluff / Over-Call (+12.8%)**</span> | 実測 **74.0%** (±4.6%) vs GTO 61.2% *(n=339)*
* **BBのCall (vs SBのエクスプロイト)** : <span style='color: red'>**🟥 Over-Bluff / Over-Call (+11.0%)**</span> | 実測 **51.7%** (±6.8%) vs GTO 40.7% *(n=201)*

### 2. **pp13386305** (`13386305`) - 5,181 Hands
* **SBの先制Push (対BBのエクスプロイト)** : <span style='color: gray'>GTO付近 (-3.3%)</span> | 実測 **57.9%** (±5.5%) vs GTO 61.2% *(n=309)*
* **BBのCall (vs SBのエクスプロイト)** : <span style='color: gray'>GTO付近 (+1.1%)</span> | 実測 **41.9%** (±7.6%) vs GTO 40.7% *(n=160)*

### 3. **pp13386498** (`13386498`) - 4,241 Hands
* **SBの先制Push (対BBのエクスプロイト)** : <span style='color: gray'>GTO付近 (+0.8%)</span> | 実測 **62.0%** (±5.8%) vs GTO 61.2% *(n=266)*
* **BBのCall (vs SBのエクスプロイト)** : <span style='color: gray'>GTO付近 (+5.9%)</span> | 実測 **46.7%** (±8.3%) vs GTO 40.7% *(n=135)*

### 4. *(名前未取得)* (`13337673`) - 3,797 Hands
* **SBの先制Push (対BBのエクスプロイト)** : <span style='color: gray'>GTO付近 (-2.1%)</span> | 実測 **59.1%** (±5.9%) vs GTO 61.2% *(n=264)*
* **BBのCall (vs SBのエクスプロイト)** : <span style='color: gray'>GTO付近 (-4.9%)</span> | 実測 **35.8%** (±7.3%) vs GTO 40.7% *(n=162)*

### 5. **イッッシー** (`13352580`) - 3,586 Hands
* **SBの先制Push (対BBのエクスプロイト)** : <span style='color: green'>**🟩 Under-Bluff / Under-Call (-21.8%)**</span> | 実測 **39.5%** (±7.8%) vs GTO 61.2% *(n=147)*
* **BBのCall (vs SBのエクスプロイト)** : <span style='color: gray'>GTO付近 (-5.7%)</span> | 実測 **35.0%** (±9.2%) vs GTO 40.7% *(n=100)*

### 6. **pp13082796** (`13082796`) - 3,567 Hands
* **SBの先制Push (対BBのエクスプロイト)** : <span style='color: green'>**🟩 Under-Bluff / Under-Call (-17.8%)**</span> | 実測 **43.4%** (±6.1%) vs GTO 61.2% *(n=249)*
* **BBのCall (vs SBのエクスプロイト)** : <span style='color: gray'>GTO付近 (-7.9%)</span> | 実測 **32.9%** (±7.5%) vs GTO 40.7% *(n=149)*

### 7. **pp012343210** (`11016196`) - 3,430 Hands
* **SBの先制Push (対BBのエクスプロイト)** : <span style='color: green'>**🟩 Under-Bluff / Under-Call (-19.8%)**</span> | 実測 **41.4%** (±6.4%) vs GTO 61.2% *(n=222)*
* **BBのCall (vs SBのエクスプロイト)** : <span style='color: gray'>GTO付近 (-3.0%)</span> | 実測 **37.7%** (±9.1%) vs GTO 40.7% *(n=106)*

### 8. **ラクダくん** (`13308748`) - 3,032 Hands
* **SBの先制Push (対BBのエクスプロイト)** : <span style='color: red'>**🟥 Over-Bluff / Over-Call (+5.7%)**</span> | 実測 **66.9%** (±5.4%) vs GTO 61.2% *(n=293)*
* **BBのCall (vs SBのエクスプロイト)** : <span style='color: gray'>GTO付近 (+7.4%)</span> | 実測 **48.2%** (±7.6%) vs GTO 40.7% *(n=164)*

### 9. **pp13276158** (`13276158`) - 3,002 Hands
* **SBの先制Push (対BBのエクスプロイト)** : <span style='color: green'>**🟩 Under-Bluff / Under-Call (-22.1%)**</span> | 実測 **39.1%** (±7.1%) vs GTO 61.2% *(n=179)*
* **BBのCall (vs SBのエクスプロイト)** : <span style='color: gray'>GTO付近 (-10.7%)</span> | 実測 **30.0%** (±10.5%) vs GTO 40.7% *(n=70)*

### 10. **Centurio.** (`8994464`) - 2,189 Hands
* **SBの先制Push (対BBのエクスプロイト)** : <span style='color: green'>**🟩 Under-Bluff / Under-Call (-25.9%)**</span> | 実測 **35.3%** (±8.6%) vs GTO 61.2% *(n=116)*
* **BBのCall (vs SBのエクスプロイト)** : <span style='color: gray'>GTO付近 (-1.0%)</span> | 実測 **39.7%** (±11.0%) vs GTO 40.7% *(n=73)*

### 11. **KBum** (`13038175`) - 2,152 Hands
* **SBの先制Push (対BBのエクスプロイト)** : <span style='color: green'>**🟩 Under-Bluff / Under-Call (-11.6%)**</span> | 実測 **49.6%** (±8.3%) vs GTO 61.2% *(n=137)*
* **BBのCall (vs SBのエクスプロイト)** : <span style='color: gray'>GTO付近 (+5.2%)</span> | 実測 **45.9%** (±12.1%) vs GTO 40.7% *(n=61)*

### 12. **お風呂好き？🤴** (`11364420`) - 1,882 Hands
* **SBの先制Push (対BBのエクスプロイト)** : <span style='color: gray'>GTO付近 (+1.0%)</span> | 実測 **62.2%** (±9.4%) vs GTO 61.2% *(n=98)*
* **BBのCall (vs SBのエクスプロイト)** : <span style='color: gray'>GTO付近 (-7.4%)</span> | 実測 **33.3%** (±11.6%) vs GTO 40.7% *(n=60)*

### 13. **pp1334901** (`3971287`) - 1,831 Hands
* **SBの先制Push (対BBのエクスプロイト)** : <span style='color: gray'>GTO付近 (+5.2%)</span> | 実測 **66.5%** (±7.3%) vs GTO 61.2% *(n=158)*
* **BBのCall (vs SBのエクスプロイト)** : <span style='color: gray'>GTO付近 (+3.3%)</span> | 実測 **44.0%** (±10.4%) vs GTO 40.7% *(n=84)*

### 14. **pp13126155** (`13126155`) - 1,551 Hands
* **SBの先制Push (対BBのエクスプロイト)** : <span style='color: gray'>GTO付近 (-6.7%)</span> | 実測 **54.5%** (±10.2%) vs GTO 61.2% *(n=88)*
* **BBのCall (vs SBのエクスプロイト)** : <span style='color: gray'>GTO付近 (-2.4%)</span> | 実測 **38.3%** (±11.9%) vs GTO 40.7% *(n=60)*

### 15. *(名前未取得)* (`13112931`) - 1,542 Hands
* **SBの先制Push (対BBのエクスプロイト)** : <span style='color: gray'>GTO付近 (-2.7%)</span> | 実測 **58.6%** (±9.0%) vs GTO 61.2% *(n=111)*
* **BBのCall (vs SBのエクスプロイト)** : <span style='color: gray'>GTO付近 (+0.6%)</span> | 実測 **41.4%** (±12.3%) vs GTO 40.7% *(n=58)*
