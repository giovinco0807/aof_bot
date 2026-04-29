### トッププレイヤーの GTO 乖離分析 (4-MAX) ※95%信頼区間つき

> [!TIP]
> 各数値の下にある `±X.X%` は**統計学的な誤差範囲（95%信頼区間）**です。
> 相手の傾向が完全にGTOから外れている（エクスプロイト可能）な箇所は、**<span style='color: red'>赤（ルースすぎ）</span>と<span style='color: green'>緑（タイトすぎ）</span>で色付け**されています。

| Player | DB Raw Hands | CO Open<br>(GTO 29.1%) | BTN Open<br>(GTO 34.3%) | SB Open<br>(GTO 61.2%) | BB Call vs SB<br>(GTO 40.7%) |
|---|---|---|---|---|---|
| Hero (Bot)<br>`13268363` | 15,537 | **29.5%** (±2.3%)<br><span style='color: gray'>+0.4%</span><br>*(n=1572)* | **32.5%** (±2.8%)<br><span style='color: gray'>-1.8%</span><br>*(n=1074)* | **60.1%** (±3.8%)<br><span style='color: gray'>-1.2%</span><br>*(n=646)* | **40.1%** (±4.8%)<br><span style='color: gray'>-0.6%</span><br>*(n=399)* |
| **キングジャック**<br>`13082001` | 4,995 | **32.6%** (±3.5%)<br><span style='color: red'>+3.5% **(Over-Bluff)**</span><br>*(n=702)* | **42.1%** (±4.4%)<br><span style='color: red'>+7.8% **(Over-Bluff)**</span><br>*(n=484)* | **74.6%** (±4.9%)<br><span style='color: red'>+13.4% **(Over-Bluff)**</span><br>*(n=295)* | **51.2%** (±7.4%)<br><span style='color: red'>+10.4% **(Over-Bluff)**</span><br>*(n=172)* |
| **pp13386305**<br>`13386305` | 4,666 | **31.6%** (±3.5%)<br><span style='color: gray'>+2.5%</span><br>*(n=678)* | **34.8%** (±4.3%)<br><span style='color: gray'>+0.5%</span><br>*(n=460)* | **57.1%** (±5.7%)<br><span style='color: gray'>-4.1%</span><br>*(n=287)* | **42.1%** (±7.8%)<br><span style='color: gray'>+1.4%</span><br>*(n=152)* |
| *(名前未取得)*<br>`13337673` | 3,797 | **27.7%** (±3.5%)<br><span style='color: gray'>-1.4%</span><br>*(n=618)* | **34.6%** (±4.4%)<br><span style='color: gray'>+0.3%</span><br>*(n=436)* | **59.1%** (±5.9%)<br><span style='color: gray'>-2.1%</span><br>*(n=264)* | **35.8%** (±7.3%)<br><span style='color: gray'>-4.9%</span><br>*(n=162)* |
| **pp012343210**<br>`11016196` | 3,430 | **24.3%** (±3.9%)<br><span style='color: green'>-4.8% **(Under-Bluff)**</span><br>*(n=457)* | **27.9%** (±4.9%)<br><span style='color: green'>-6.4% **(Under-Bluff)**</span><br>*(n=323)* | **41.4%** (±6.4%)<br><span style='color: green'>-19.8% **(Under-Bluff)**</span><br>*(n=222)* | **37.7%** (±9.1%)<br><span style='color: gray'>-3.0%</span><br>*(n=106)* |
| **pp13082796**<br>`13082796` | 3,429 | **26.8%** (±3.7%)<br><span style='color: gray'>-2.3%</span><br>*(n=541)* | **36.3%** (±4.9%)<br><span style='color: gray'>+2.0%</span><br>*(n=369)* | **43.0%** (±6.3%)<br><span style='color: green'>-18.2% **(Under-Bluff)**</span><br>*(n=237)* | **31.9%** (±7.6%)<br><span style='color: green'>-8.8% **(Under-Bluff)**</span><br>*(n=141)* |