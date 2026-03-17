# AoF Bot

PPPoker All-or-Fold 自動プレイボット

## 起動方法

### 1テーブル
```
start_bot.bat
```
または
```
python automation\gui.py
```

### 2テーブル
```
start_bot_2table.bat
```
または
```
python automation\gui_2table.py
```

## 必要な設定

- **Hero UID**: GUIの「Hero UID」に自分のPPPoker UIDを入力
- **Auto-Play**: チェックを入れると自動でFold/All-inをクリック
- **Delay Tuning**: スライダーでクリック遅延を調整（Min/Max/Hesitation）

## ファイル構成

| ファイル | 説明 |
|---------|------|
| `automation/gui.py` | 1テーブル用GUI |
| `automation/gui_2table.py` | 2テーブル用GUI（GTO並列表示） |
| `automation/pc_input.py` | ボタン検出・クリック制御 |
| `automation/gto_lookup.py` | GTOチャート参照 |
| `automation/cloud_db.py` | Supabaseクラウド同期 |
| `hook/packet_capture.py` | Fridaパケットキャプチャ（メイン） |
| `automation/data/pc_config.json` | ボタン座標・遅延設定 |
| `automation/data/cloud_config.json` | Supabase接続設定 |
