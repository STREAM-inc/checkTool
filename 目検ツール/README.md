# 目検ツール

## インストール

必要なもの：Windows 10 / 11、Python 3.8 以上（`py` または `python` コマンドが使えること）

1. リポジトリ全体を取得する（隣の `郵便番号補完ツール` のデータも使うため）

   ```bash
   git clone https://github.com/STREAM-inc/checkTool.git
   ```

2. `checkTool` の直下の `APIキー.example.txt` を `APIキー.txt` にコピーし、中身を自分の Backlog の API キー 1 行だけにする

## 設定

設定は `checkTool` 直下の `setting.json` の `"目検ツール"` に書きます（`setting.example.json` をコピーして作る。無くても初期値で動きます）。
相対パスは `checkTool` フォルダから。書き方は [ルートの README](../README.md#設定) を見てください。

| 設定 | 初期値 | 内容 |
| --- | --- | --- |
| `BACKLOG_SPACE` | `https://streeeeeam.backlog.com` | Backlog スペースの URL |
| `APIKEY_FILE` | `APIキー.txt` | API キーを書いたファイル |
| `CHECKS_DIR` | `目検ツール/checks` | チェック状態の保存先。共有フォルダにするとオーナーと同じ状態を見られる |
| `TASKS_FILE` | `目検ツール/tasks.json` | 左端のタスクのタブの保存先（自分用） |
| `CUSTOMER_RULES` | `目検ツール/顧客ルール.json` | 顧客ルールのファイル。共有フォルダに置くとみんなで同じルールを見られる |
| `PORT` | `8765` | ブラウザで開くポート |
| `HW_CSV` | （空欄） | 市外局番チェックの参考にするハローワーク CSV。空欄なら「採用担当者名寄せ」の最初の CSV を使う |

起動したときの黒いウィンドウに「設定ファイル: …」と、どの設定を読んだかが出ます。

## 起動

`checkTool` 直下の `目検ツール.bat` をダブルクリック → ブラウザで `http://localhost:8765` が開きます。
黒いウィンドウを閉じると終了します。
