## 常連プレイヤー（1,500ハンド以上）のエクスプロイト・リーク分析

以下は、データベース上で **サンプルサイズが大きく、かつ統計的信頼区間（95%）を完全に逸脱している（明らかにGTOからズレている）** シチュエーションだけを抽出した「弱点一覧（リーク箇所）」です。

> [!TIP]
> * <span style='color: red'>**🟥 Over-Bluff / Over-Call**</span>：GTOよりプレイしすぎ。このアクションには通常より**広くパニッシュ**（コールやPush）できます。
> * <span style='color: green'>**🟩 Under-Bluff / Under-Call**</span>：GTOよりプレイしなさすぎ。このアクションには**降りすぎ推奨**、または相手のブラインドを**広くスチール**できます。

### 1. Hero (Bot) `13268363` - 17,604 Hands
* 📊 *統計的に有意な大きなリーク（GTOからの逸脱）は見つかりませんでした。非常にバランスの取れたプレイをしています。*

### 2. キングジャック `13082001` - 5,513 Hands
* <span style='color: red'>**🟥 ルースすぎ (+3.8%)**</span> : **COからの先制Push** | 実測 **32.9%** (±3.3%) ＞ GTO 29.1% *(n=799)*
* <span style='color: red'>**🟥 ルースすぎ (+6.7%)**</span> : **BTNからの先制Push** | 実測 **41.0%** (±4.1%) ＞ GTO 34.3% *(n=553)*
* <span style='color: red'>**🟥 ルースすぎ (+12.8%)**</span> : **SBからの先制Push** | 実測 **74.0%** (±4.6%) ＞ GTO 61.2% *(n=339)*
* <span style='color: red'>**🟥 ルースすぎ (+8.0%)**</span> : **BTNのCall (vs CO)** | 実測 **21.1%** (±5.1%) ＞ GTO 13.1% *(n=246)*
* <span style='color: red'>**🟥 ルースすぎ (+11.7%)**</span> : **SBのCall (vs CO)** | 実測 **28.6%** (±6.4%) ＞ GTO 16.9% *(n=189)*
* <span style='color: red'>**🟥 ルースすぎ (+11.4%)**</span> : **SBのCall (vs BTN)** | 実測 **32.1%** (±6.2%) ＞ GTO 20.7% *(n=218)*
* <span style='color: red'>**🟥 ルースすぎ (+11.0%)**</span> : **BBのCall (vs SB)** | 実測 **51.7%** (±6.8%) ＞ GTO 40.7% *(n=201)*

### 3. pp13386305 `13386305` - 5,181 Hands
* <span style='color: red'>**🟥 ルースすぎ (+8.4%)**</span> : **BBのCall (vs BTN)** | 実測 **35.0%** (±7.7%) ＞ GTO 26.6% *(n=143)*

### 4. pp13386498 `13386498` - 4,241 Hands
* <span style='color: red'>**🟥 ルースすぎ (+11.9%)**</span> : **BTNのCall (vs CO)** | 実測 **25.0%** (±6.4%) ＞ GTO 13.1% *(n=176)*

### 5. *(名前未取得)* `13337673` - 3,797 Hands
* 📊 *統計的に有意な大きなリーク（GTOからの逸脱）は見つかりませんでした。非常にバランスの取れたプレイをしています。*

### 6. イッッシー `13352580` - 3,586 Hands
* <span style='color: green'>**🟩 タイトすぎ (-21.8%)**</span> : **SBからの先制Push** | 実測 **39.5%** (±7.8%) ＜ GTO 61.2% *(n=147)*
* <span style='color: red'>**🟥 ルースすぎ (+18.5%)**</span> : **BTNのCall (vs CO)** | 実測 **31.6%** (±9.1%) ＞ GTO 13.1% *(n=98)*
* <span style='color: red'>**🟥 ルースすぎ (+11.1%)**</span> : **SBのCall (vs BTN)** | 実測 **31.8%** (±9.7%) ＞ GTO 20.7% *(n=85)*

### 7. pp13082796 `13082796` - 3,567 Hands
* <span style='color: green'>**🟩 タイトすぎ (-17.8%)**</span> : **SBからの先制Push** | 実測 **43.4%** (±6.1%) ＜ GTO 61.2% *(n=249)*
* <span style='color: red'>**🟥 ルースすぎ (+8.1%)**</span> : **SBのCall (vs CO)** | 実測 **25.0%** (±6.7%) ＞ GTO 16.9% *(n=160)*

### 8. pp012343210 `11016196` - 3,430 Hands
* <span style='color: green'>**🟩 タイトすぎ (-4.8%)**</span> : **COからの先制Push** | 実測 **24.3%** (±3.9%) ＜ GTO 29.1% *(n=457)*
* <span style='color: green'>**🟩 タイトすぎ (-6.4%)**</span> : **BTNからの先制Push** | 実測 **27.9%** (±4.9%) ＜ GTO 34.3% *(n=323)*
* <span style='color: green'>**🟩 タイトすぎ (-19.8%)**</span> : **SBからの先制Push** | 実測 **41.4%** (±6.4%) ＜ GTO 61.2% *(n=222)*

### 9. ラクダくん `13308748` - 3,032 Hands
* <span style='color: red'>**🟥 ルースすぎ (+5.6%)**</span> : **BTNからの先制Push** | 実測 **39.9%** (±4.6%) ＞ GTO 34.3% *(n=434)*
* <span style='color: red'>**🟥 ルースすぎ (+5.7%)**</span> : **SBからの先制Push** | 実測 **66.9%** (±5.4%) ＞ GTO 61.2% *(n=293)*

### 10. pp13276158 `13276158` - 3,002 Hands
* <span style='color: green'>**🟩 タイトすぎ (-22.1%)**</span> : **SBからの先制Push** | 実測 **39.1%** (±7.1%) ＜ GTO 61.2% *(n=179)*
* <span style='color: red'>**🟥 ルースすぎ (+13.3%)**</span> : **SBのCall (vs BTN)** | 実測 **34.0%** (±9.1%) ＞ GTO 20.7% *(n=100)*

### 11. Centurio. `8994464` - 2,189 Hands
* <span style='color: green'>**🟩 タイトすぎ (-25.9%)**</span> : **SBからの先制Push** | 実測 **35.3%** (±8.6%) ＜ GTO 61.2% *(n=116)*
* <span style='color: red'>**🟥 ルースすぎ (+13.1%)**</span> : **BTNのCall (vs CO)** | 実測 **26.2%** (±10.8%) ＞ GTO 13.1% *(n=61)*

### 12. KBum `13038175` - 2,152 Hands
* <span style='color: red'>**🟥 ルースすぎ (+7.5%)**</span> : **COからの先制Push** | 実測 **36.6%** (±5.5%) ＞ GTO 29.1% *(n=287)*
* <span style='color: red'>**🟥 ルースすぎ (+7.2%)**</span> : **BTNからの先制Push** | 実測 **41.5%** (±6.8%) ＞ GTO 34.3% *(n=200)*
* <span style='color: green'>**🟩 タイトすぎ (-11.6%)**</span> : **SBからの先制Push** | 実測 **49.6%** (±8.3%) ＜ GTO 61.2% *(n=137)*
* <span style='color: red'>**🟥 ルースすぎ (+17.9%)**</span> : **BTNのCall (vs CO)** | 実測 **31.0%** (±9.7%) ＞ GTO 13.1% *(n=84)*
* <span style='color: red'>**🟥 ルースすぎ (+21.5%)**</span> : **SBのCall (vs CO)** | 実測 **38.3%** (±11.9%) ＞ GTO 16.9% *(n=60)*

### 13. お風呂好き？🤴 `11364420` - 1,882 Hands
* <span style='color: red'>**🟥 ルースすぎ (+12.9%)**</span> : **BTNのCall (vs CO)** | 実測 **26.0%** (±9.9%) ＞ GTO 13.1% *(n=73)*
* <span style='color: red'>**🟥 ルースすぎ (+13.4%)**</span> : **SBのCall (vs BTN)** | 実測 **34.1%** (±13.5%) ＞ GTO 20.7% *(n=44)*

### 14. pp1334901 `3971287` - 1,831 Hands
* <span style='color: red'>**🟥 ルースすぎ (+6.9%)**</span> : **COからの先制Push** | 実測 **36.0%** (±5.1%) ＞ GTO 29.1% *(n=339)*
* <span style='color: red'>**🟥 ルースすぎ (+20.6%)**</span> : **BTNからの先制Push** | 実測 **54.9%** (±6.3%) ＞ GTO 34.3% *(n=233)*
* <span style='color: red'>**🟥 ルースすぎ (+11.6%)**</span> : **BTNのCall (vs CO)** | 実測 **24.7%** (±8.5%) ＞ GTO 13.1% *(n=97)*
* <span style='color: red'>**🟥 ルースすぎ (+16.0%)**</span> : **SBのCall (vs CO)** | 実測 **32.9%** (±10.3%) ＞ GTO 16.9% *(n=76)*
* <span style='color: red'>**🟥 ルースすぎ (+11.0%)**</span> : **SBのCall (vs BTN)** | 実測 **31.6%** (±10.1%) ＞ GTO 20.7% *(n=79)*
* <span style='color: red'>**🟥 ルースすぎ (+18.0%)**</span> : **BBのCall (vs BTN)** | 実測 **44.6%** (±11.7%) ＞ GTO 26.6% *(n=65)*

### 15. pp13126155 `13126155` - 1,551 Hands
* <span style='color: red'>**🟥 ルースすぎ (+15.4%)**</span> : **BTNからの先制Push** | 実測 **49.7%** (±7.3%) ＞ GTO 34.3% *(n=177)*
* <span style='color: red'>**🟥 ルースすぎ (+10.2%)**</span> : **BTNのCall (vs CO)** | 実測 **23.3%** (±10.5%) ＞ GTO 13.1% *(n=60)*

### 16. *(名前未取得)* `13112931` - 1,542 Hands
* <span style='color: red'>**🟥 ルースすぎ (+14.1%)**</span> : **BTNのCall (vs CO)** | 実測 **27.2%** (±9.5%) ＞ GTO 13.1% *(n=81)*
* <span style='color: red'>**🟥 ルースすぎ (+10.3%)**</span> : **SBのCall (vs CO)** | 実測 **27.1%** (±10.2%) ＞ GTO 16.9% *(n=70)*
* <span style='color: red'>**🟥 ルースすぎ (+19.8%)**</span> : **BBのCall (vs BTN)** | 実測 **46.3%** (±14.6%) ＞ GTO 26.6% *(n=41)*
