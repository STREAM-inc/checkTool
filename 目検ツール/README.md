# 目検ツール

## インストール

必要なもの：Windows 10 / 11、Python 3.8 以上（`py` または `python` コマンドが使えること）

1. リポジトリ全体を取得する（隣の `郵便番号補完ツール` のデータも使うため）

   ```bash
   git clone https://github.com/STREAM-inc/checkTool.git 3-目検
   ```

2. `3-目検` の直下の `APIキー.example.txt` を `APIキー.txt` にコピーし、中身を自分の Backlog の API キー 1 行だけにする

## 設定

設定ファイルが無くても初期値で動きます。変えたいときだけ `設定.example.txt` を `設定.txt` にコピーして書き換えてください。

| 設定 | 初期値 | 内容 |
| --- | --- | --- |
| `BACKLOG_SPACE` | `https://streeeeeam.backlog.com` | Backlog スペースの URL |
| `APIKEY_FILE` | `..\APIキー.txt` | API キーを書いたファイル |
| `CHECKS_DIR` | `checks` | チェック状態の保存先。共有フォルダにするとオーナーと同じ状態を見られる |
| `TASKS_FILE` | `tasks.json` | 左端のタスクのタブの保存先（自分用） |
| `PORT` | `8765` | ブラウザで開くポート |
| `HW_CSV` | （空欄） | 市外局番チェックの参考にするハローワーク CSV |

## 起動

`目検ツール.bat` をダブルクリック → ブラウザで `http://localhost:8765` が開きます。
黒いウィンドウを閉じると終了します。
